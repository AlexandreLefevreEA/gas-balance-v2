"""Kpler plant-availability connector (vintages) — the `asOf` snapshots of the forward view.

Source: `GET /power/outages/availability/fuel-types` with the **`asOf`** (vintage) param — the
**forward** counterpart of `kpler_availability`. The same daily available capacity (MW) per
country and fuel (coal, gas, lignite, nuclear), but captured *as it was known at a given
`asOf`*: each snapshot carries the planned-outage outlook (delivery dates from `asOf` forward).
One series per `(zone, fuel)`, code `KP.AVAILFC.<FUEL>.<zone>`; `sub_group` holds the fuel (so a
vintage joins its actual `KP.AVAIL.<FUEL>.<zone>` cleanly). We store the `central` estimate.

**The vintage dimension.** A snapshot is `(asOf, deliveryDate) → value`: the same delivery day
appears in every daily snapshot. So values land in `forecast_covariate`, keyed by
`(series_id, made_on, ts)` (`made_on` = the `asOf` date), not the single-vintage `covariate`.
There is **no model dimension** (unlike `kpler_generation_forecast`). See ADR 0009.

**Retention.** Multi-vintage storage is bounded by the shared rule enforced after every load (and
re-runnable via `etl prune kpler_availability_forecast`): keep **all** snapshots of the last 15
days, plus **every Monday** snapshot for 1 year; delete the rest. Same rule as the other
`*_forecast` connectors.

**Refresh: self-managing, and backfills missing vintages.** Each run computes the desired keep-set
of `asOf` dates, subtracts what's already stored, and fetches every missing vintage plus a small
recent overlap re-pulled for revisions. Historical `asOf` snapshots go back well past the
trailing-year keep-set (probed to 2024), so no history floor is needed.

**Request grain.** `zones` and `fuelTypes` both batch into one request, so we issue **one request
per `asOf`** (over a forward delivery horizon) — fanned out **async** (bounded by `_CONCURRENCY`)
over the shared 429/5xx retry. Auth: HTTP Basic with `KPLER_API_KEY_V2`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_common import (
    desired_run_dates,
    loaded_run_dates,
    prune_vintages,
    vintages_to_delete,
)
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.forecast_covariate import forecast_covariate_availability_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_availability_forecast"
schema = forecast_covariate_availability_schema

_ENDPOINT = "power/outages/availability/fuel-types"
# Our four fuel series: code -> display name (sub_group = code.lower()). Same as kpler_availability
# so a vintage lines up with its actual.
_FUELS = {"COAL": "Coal", "GAS": "Gas", "LIGNITE": "Lignite", "NUCLEAR": "Nuclear"}
# Kpler `fuelType` enum -> our fuel code; also the request list. 1:1, no folding.
_KPLER_FUEL_TO_CODE = {
    "fossil hard coal": "COAL",
    "fossil gas": "GAS",
    "fossil brown coal/lignite": "LIGNITE",
    "nuclear": "NUCLEAR",
}
_KPLER_FUELS = list(_KPLER_FUEL_TO_CODE)
# Forward delivery horizon per snapshot, in days. Each `asOf` captures availability from `asOf`
# out this far — long enough for the planned-outage view (nuclear maintenance is scheduled
# ~12 mo ahead) without storing the multi-year tail every vintage. Widen to 730 (the +24-mo
# long-term convention) if the forecast needs a longer view; it just scales row volume.
_HORIZON_DAYS = 365
# Re-pull the most recent few snapshots each run, to catch revised outlooks.
_REFRESH_DAYS = 3
# ponytail: bound the concurrent in-flight requests. `zones` + `fuelTypes` batch into one request
# per `asOf`, so a run is at most ~67 GETs on the first backfill (15 daily + 52 Mondays) and a
# handful incrementally — fan them out instead of looping serially, capped so we don't trip the
# rate limit (the shared helper still retries 429/5xx). Lower if you see sustained 429s.
_CONCURRENCY = 6
# ponytail: no _HISTORY_START floor (unlike kpler_generation_forecast). `asOf` snapshots go back
# past the trailing-year keep-set (probed to 2024-01), so every Monday vintage we request returns
# data — no empty-forever re-fetch.


def _code(zone: str, fuel: str) -> str:
    """Series code, e.g. KP.AVAILFC.NUCLEAR.FR / KP.AVAILFC.LIGNITE.DE."""
    return f"KP.AVAILFC.{fuel}.{zone}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x fuel list -> one vintaged series per (zone, fuel)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], fuel),
            "name": f"{z['zone']} {disp} availability outlook",
            "group": "availability_forecast",
            "sub_group": fuel.lower(),
            "area": z["area"],
            "unit": "MW",
            "zone": z["zone"],  # used by to_canonical merge + fetch
            "fuel": fuel,  # used by to_canonical merge
        }
        for z in zones
        for fuel, disp in _FUELS.items()
    ]


def _desired_run_dates(today: dt.date) -> list[dt.date]:
    """This connector's fetch keep-set = the shared retention rule (no history floor)."""
    return desired_run_dates(today)


def _vintages_to_delete(made_ons: list[dt.date], today: dt.date) -> set[dt.date]:
    """This connector's retention rule (shared, pure); `prune` wraps it in SQL."""
    return vintages_to_delete(made_ons, today)


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route availability-vintage rows to `forecast_covariate`, then enforce retention.

    The prune runs in the same transaction (the CLI commits on success), so a failed load
    rolls back both. Imported lazily so importing the connector (for the registry) is DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_forecast_covariates

    written = upsert_forecast_covariates(session, df, run_id, code_to_id)
    prune(session)
    return written


def prune(session: Session) -> int:
    """Delete vintages outside the retention window (shared rule). Returns rows deleted."""
    return prune_vintages(session, source)


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Fetch every desired `asOf` snapshot not already stored (+ a recent refresh overlap).

    `since` (framework contract) is ignored — the window is the retention keep-set,
    self-determined from what `forecast_covariate` already holds, so gaps are backfilled.
    """
    del since
    cfg = get_kpler_settings()
    zones = sorted({e["zone"] for e in series_dict()})
    today = dt.datetime.now(dt.UTC).date()

    desired = set(_desired_run_dates(today))
    have = loaded_run_dates(source)
    refresh = {today - dt.timedelta(days=i) for i in range(_REFRESH_DAYS)}
    # clamp refresh to the keep-set: never re-fetch a vintage that just aged out of the window
    # (only for prune to delete it next run)
    as_ofs = sorted((desired - have) | (desired & refresh))

    cols = ["zone", "fuelType", "date", "value", "made_on"]
    if not as_ofs:
        return pd.DataFrame(columns=cols)
    log.info(
        "kpler-availfc: %d asOf-snapshots x %d zones x %d fuels (%dd horizon), <=%d concurrent "
        "(today %s)",
        len(as_ofs),
        len(zones),
        len(_KPLER_FUELS),
        _HORIZON_DAYS,
        _CONCURRENCY,
        today,
    )

    rows = asyncio.run(_fetch_rows(cfg, as_ofs, zones))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # Kpler returns tz-aware UTC ISO (daily 00:00); canonical `date` is naive UTC.
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
        df["made_on"] = pd.to_datetime(df["made_on"])  # 'YYYY-MM-DD' -> naive datetime64
    return df


async def _fetch_rows(
    cfg: KplerSettings, as_ofs: list[dt.date], zones: list[str]
) -> list[tuple[str, str, str, float, str]]:
    """Fan out one request per `asOf` concurrently (bounded), collect raw rows.

    Each request batches all zones + fuels over the forward delivery horizon `[asOf, asOf +
    _HORIZON_DAYS)`; the response echoes `zone` and `fuelType` but the `asOf` we tag ourselves
    (the date we requested). We keep the `central` estimate (drop null). A `_CONCURRENCY`
    semaphore caps in-flight requests; `arequest` handles 429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(as_of: dt.date) -> list[tuple[str, str, str, float, str]]:
            end = as_of + dt.timedelta(days=_HORIZON_DAYS)
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "zones": zones,
                        "fuelTypes": _KPLER_FUELS,
                        "granularity": "daily",
                        "timezone": "UTC",
                        "startDate": as_of.isoformat(),
                        "endDate": end.isoformat(),
                        "asOf": as_of.isoformat(),
                    },
                    label="kpler-availfc",
                )
            made_on = as_of.isoformat()
            return [
                (d["zone"], d["fuelType"], d["startDate"], d["central"], made_on)
                for d in resp.json().get("data", [])
                if d.get("central") is not None
            ]

        out = await asyncio.gather(*(one(a) for a in as_ofs))
    return [row for chunk in out for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map (zone, fuelType) daily vintage rows to canonical series, carrying `made_on`.

    Maps the Kpler fuelType to our code (dropping unmapped fuels / nulls), keeps only known
    (zone, fuel) series. Fuels are 1:1 (no folding), so (zone, fuel, day, vintage) is unique.
    """
    out_cols = [
        "date",
        "series_id",
        "name",
        "group",
        "sub_group",
        "area",
        "value",
        "source",
        "made_on",
    ]
    meta = pd.DataFrame(
        [
            {
                "zone": e["zone"],
                "fuel": e["fuel"],
                "series_id": e["code"],
                "name": e["name"],
                "group": e["group"],
                "sub_group": e["sub_group"],
                "area": e["area"],
            }
            for e in series_dict()
        ]
    )
    df = raw.copy()
    df["fuel"] = df["fuelType"].map(_KPLER_FUEL_TO_CODE)  # unmapped fuels -> NaN
    df = df[df["fuel"].notna() & df["value"].notna()]
    df = df.merge(meta, on=["zone", "fuel"], how="inner")  # unknown zones drop out
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["value"] = df["value"].astype(float)
    df["source"] = source
    return df[out_cols]
