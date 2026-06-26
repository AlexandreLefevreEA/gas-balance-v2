"""Kpler generation-forecast connector — the generation analogue of `kpler_temps_forecast`.

Source: `GET /power/generations/forecasts`, hourly generation (MW) per power zone, fuel and
weather model — the **forecast** counterpart of `kpler_generation_actual`. We keep the same
four fuels (solar, wind, run-of-river, gas; Kpler's `wind onshore` + `wind offshore` are
summed into one WIND series) and the two 00z models of `kpler_temps_forecast`:

- **EC_AIFS_ENS** — the AI (AIFS) ensemble, ~15-day horizon ("AI EC ENS").
- **EC_46** — the 46-day extended forecast (published with a ~1-day lag; the refresh overlap
  picks it up).

One series per (zone x fuel x model), code `KP.GENFC.<FUEL>.<zone>.<MODEL>`; `sub_group`
holds the fuel (so a forecast joins its actual `KP.GEN.<FUEL>.<zone>` cleanly).

**The vintage dimension.** A forecast is `(runDate, deliveryDate) → value`: the same delivery
hour appears in every daily run. So values land in `forecast_covariate`, keyed by
`(series_id, made_on, ts)` (`made_on` = the run date), not the single-vintage `covariate`.
See ADR 0009.

**Retention.** Multi-vintage storage is bounded by a rule enforced after every load (and
re-runnable via `etl prune kpler_generation_forecast`): keep **all** runs of the last 15 days,
plus **every Monday** run for 1 year; delete the rest.

**Refresh: self-managing, and backfills missing vintages.** Each run computes the desired
keep-set of run dates (= the retention set, clamped to `_HISTORY_START`), subtracts what's
already stored, and fetches every missing vintage plus a small recent overlap re-pulled for
revisions / the late EC_46 run.

**Request grain (API constraint).** Unlike the temperature-forecast endpoint, this one takes
**one `zone` and one `fuelType` per request** (only `models` is a list, both returned at once)
and needs `run=00z` (else it returns all four sub-daily runs). So we issue one request per
(run date x zone x fuel). Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other
Kpler connectors).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any, cast

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_common import (
    desired_run_dates,
    loaded_run_dates,
    vintages_to_delete,
)
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.forecast_covariate import forecast_covariate_generation_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_generation_forecast"
schema = forecast_covariate_generation_schema

_ENDPOINT = "power/generations/forecasts"
# The two 00z models we keep (same as kpler_temps_forecast). Passed as a list — the endpoint
# returns both in one request and echoes `model` per row.
_MODELS = ["EC_AIFS_ENS", "EC_46"]
# Our four fuel series: code -> display name (sub_group = code.lower()). Same as
# kpler_generation_actual so a forecast lines up with its actual.
_FUELS = {"SOLAR": "Solar", "WIND": "Wind", "ROR": "Run-of-river", "GAS": "Gas"}
# Kpler `fuelType` enum -> our fuel code. Wind onshore+offshore fold into one WIND series. The
# response does NOT echo fuelType (it's a singular query param), so fetch tags rows with the
# fuel it requested; these are exactly the fuelTypes we request.
_KPLER_FUEL_TO_CODE = {
    "solar": "SOLAR",
    "wind onshore": "WIND",
    "wind offshore": "WIND",
    "hydro run-of-river and poundage": "ROR",
    "fossil gas": "GAS",
}
# Re-pull the most recent few run dates each run, to catch revised runs and the late EC_46.
_REFRESH_DAYS = 3
# ponytail: floor the fetch keep-set — generation-forecast vintages only go back to ~Jan 2026.
# Without this, the ~40 trailing-year Mondays that predate the data return empty, store
# nothing, never enter the "already loaded" set, and get re-requested (x18 zones x5 fuels)
# every run. (kpler_temps_forecast needs no floor: its history is 2+ years deep.) Widen if
# Kpler backfills. Does not touch the prune rule — retention semantics are unchanged.
_HISTORY_START = dt.date(2026, 1, 1)
# ponytail: bound the concurrent in-flight requests. The endpoint forces one (zone, fuel) per
# request, so a backfill is thousands of small GETs — fan them out instead of looping serially,
# but cap it so we don't trip the rate limit (the shared helper still retries 429/5xx). Raise if
# the API tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def _code(zone: str, fuel: str, model: str) -> str:
    """Series code, e.g. KP.GENFC.SOLAR.FR.EC_AIFS_ENS / KP.GENFC.WIND.DE.EC_46."""
    return f"KP.GENFC.{fuel}.{zone}.{model}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x fuels x models -> one series per (zone, fuel, model)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], fuel, m),
            "name": f"{z['zone']} {disp} generation forecast ({m})",
            "group": "generation_forecast",
            "sub_group": fuel.lower(),
            "area": z["area"],
            "unit": "MW",
            "zone": z["zone"],  # used by to_canonical merge + fetch
            "fuel": fuel,  # fuel CODE; used by to_canonical merge
            "model": m,  # used by to_canonical merge + fetch
        }
        for z in zones
        for fuel, disp in _FUELS.items()
        for m in _MODELS
    ]


def _desired_run_dates(today: dt.date) -> list[dt.date]:
    """This connector's fetch keep-set = the shared retention rule, floored to `_HISTORY_START`
    so we never request vintages that predate the data (they'd return empty forever)."""
    return desired_run_dates(today, floor=_HISTORY_START)


def _vintages_to_delete(made_ons: list[dt.date], today: dt.date) -> set[dt.date]:
    """This connector's retention rule (shared, pure); `prune` wraps it in SQL."""
    return vintages_to_delete(made_ons, today)


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route hourly forecast rows to `forecast_covariate`, then enforce retention.

    The prune runs in the same transaction (the CLI commits on success), so a failed load
    rolls back both. Imported lazily so importing the connector (for the registry) is DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_forecast_covariates

    written = upsert_forecast_covariates(session, df, run_id, code_to_id)
    prune(session)
    return written


def prune(session: Session) -> int:
    """Delete forecast vintages outside the retention window. Returns rows deleted."""
    from sqlalchemy import delete, select

    from gasbalance_core.models import ForecastCovariate, Series

    today = dt.datetime.now(dt.UTC).date()
    sids = select(Series.id).where(Series.source == source)
    made_ons = list(
        session.execute(
            select(ForecastCovariate.made_on)
            .where(ForecastCovariate.series_id.in_(sids))
            .distinct()
        ).scalars()
    )
    to_delete = _vintages_to_delete(made_ons, today)
    if not to_delete:
        return 0
    result = session.execute(
        delete(ForecastCovariate).where(
            ForecastCovariate.series_id.in_(sids),
            ForecastCovariate.made_on.in_(to_delete),
        )
    )
    deleted = int(cast(Any, result).rowcount or 0)  # DML CursorResult.rowcount
    log.info("kpler-gen-fc: pruned %d rows across %d vintages", deleted, len(to_delete))
    return deleted


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Fetch every desired forecast vintage not already stored (+ a recent refresh overlap).

    `since` (framework contract) is ignored — the window is the retention keep-set,
    self-determined from what `forecast_covariate` already holds, so gaps are backfilled.
    """
    del since
    cfg = get_kpler_settings()
    zones = sorted({e["zone"] for e in series_dict()})
    today = dt.datetime.now(dt.UTC).date()

    desired = set(_desired_run_dates(today))
    have = loaded_run_dates(source)
    refresh = {today - dt.timedelta(days=i) for i in range(_REFRESH_DAYS)} & desired
    run_dates = sorted((desired - have) | refresh)

    cols = ["zone", "fuelType", "model", "date", "value", "made_on"]
    if not run_dates:
        return pd.DataFrame(columns=cols)
    # One request per (run date x zone x fuel) — the endpoint takes a single zone and a single
    # fuelType (only `models` is a list). First backfill is ~run_dates x zones x fuels requests
    # (one-time, resumable via keep-set - have); routine runs are a handful. Fanned out
    # concurrently (bounded by _CONCURRENCY) over the shared 429/5xx retry/backoff.
    log.info(
        "kpler-gen-fc: %d run-dates x %d zones x %d fuels x %d models, <=%d concurrent (today %s)",
        len(run_dates),
        len(zones),
        len(_KPLER_FUEL_TO_CODE),
        len(_MODELS),
        _CONCURRENCY,
        today,
    )

    rows = asyncio.run(_fetch_rows(cfg, run_dates, zones))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # Kpler returns tz-aware UTC ISO timestamps; canonical `date` is naive UTC.
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
        df["made_on"] = pd.to_datetime(df["made_on"])  # 'YYYY-MM-DD' -> naive datetime64
    return df


async def _fetch_rows(
    cfg: KplerSettings, run_dates: list[dt.date], zones: list[str]
) -> list[tuple[str, str, str, str, float, str]]:
    """Fan out one request per (run date, zone, fuel) concurrently (bounded), collect raw rows.

    The response doesn't echo `fuelType`, so each row is tagged with the fuel its request asked
    for. Order is irrelevant — `to_canonical` groups. A `_CONCURRENCY` semaphore caps in-flight
    requests; `arequest` handles 429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)
    combos = [
        (rd, zone, fuel) for rd in run_dates for zone in zones for fuel in _KPLER_FUEL_TO_CODE
    ]

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(
            rd: dt.date, zone: str, fuel: str
        ) -> list[tuple[str, str, str, str, float, str]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "runDate": rd.isoformat(),
                        "run": "00z",
                        "zones": zone,
                        "fuelType": fuel,
                        "models": _MODELS,
                        "granularity": "hourly",
                        "timezone": "UTC",
                    },
                    label="kpler-gen-fc",
                )
            return [
                (d["zone"], fuel, d["model"], d["startDate"], d["value"], d["runDate"])
                for d in resp.json().get("data", [])
                if d.get("value") is not None
            ]

        chunks = await asyncio.gather(*(one(rd, zone, fuel) for rd, zone, fuel in combos))
    return [row for chunk in chunks for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map (zone, fuel, model) hourly forecast rows to canonical series, carrying `made_on`.

    Maps the Kpler fuelType to our code (dropping unmapped fuels / nulls), keeps only known
    (zone, fuel, model) series, then sums the fuel types that fold into one code — only WIND
    (onshore + offshore) — per (zone, code, model, hour, vintage).
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
                "model": e["model"],
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
    df = df.merge(meta, on=["zone", "fuel", "model"], how="inner")  # unknown zone/fuel/model drop
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["value"] = df["value"].astype(float)
    keys = ["date", "series_id", "name", "group", "sub_group", "area", "made_on"]
    out = df.groupby(keys, as_index=False, dropna=False)["value"].sum()  # fold wind on+off
    out["source"] = source
    return out[out_cols]
