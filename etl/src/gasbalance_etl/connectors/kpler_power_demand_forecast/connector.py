"""Kpler power-demand-forecast connector — the forecast counterpart of `kpler_power_demand`.

Source: `GET /power/loads/forecasts`, hourly electricity demand (total system load, MW) per
power zone, the **forecast** counterpart of `kpler_power_demand` (`…/power/loads/actual`). We
keep the two 00z models of the other forecast covariates:

- **EC_AIFS_ENS** — the AI (AIFS) ensemble, ~15-day horizon ("AI EC ENS").
- **EC_46** — the 46-day extended forecast (published with a ~1-day lag; the refresh overlap
  picks it up).

One series per (zone x model), code `KP.LOADFC.<zone>.<MODEL>`; `sub_group` holds the Kpler
`loadType` (`demand` = total load, the same value as the actual `KP.LOAD.<zone>` series, so a
forecast lines up with its actual; `residual_demand` is a one-line add — see `_LOAD_TYPE`).

**The vintage dimension.** A forecast is `(runDate, deliveryDate) → value`: the same delivery
hour appears in every daily run. So values land in `forecast_covariate`, keyed by
`(series_id, made_on, ts)` (`made_on` = the run date), not the single-vintage `covariate`.
See ADR 0009.

**Retention.** Multi-vintage storage is bounded by a rule enforced after every load (and
re-runnable via `etl prune kpler_power_demand_forecast`): keep **all** runs from the last 15
days, plus **every Monday** run for 1 year; delete the rest.

**Refresh: self-managing, and backfills missing vintages.** Each run computes the desired
keep-set of run dates, subtracts what's already stored, and fetches every missing vintage plus
a small recent overlap re-pulled for revisions / the late EC_46 run.

**Request grain.** Unlike `kpler_generation_forecast`, this endpoint batches **all zones**
(`zones`) and both `models` into one request, so it issues **one request per run date** (like
`kpler_temps_forecast`) — fanned out concurrently (bounded by `_CONCURRENCY`) over the shared
429/5xx retry. `zones` takes plain country codes (`DE`, not `DE-LU`). Required params the
temperature variant doesn't need: `loadType` and `models`. Auth: HTTP Basic with
`KPLER_API_KEY_V2` (shared with the other Kpler connectors).
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
from gasbalance_etl.validation.forecast_covariate import forecast_covariate_demand_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_power_demand_forecast"
schema = forecast_covariate_demand_schema

_ENDPOINT = "power/loads/forecasts"
# 00z runs of the AI ensemble (~15-day horizon) and the 46-day extended model — same two models
# as kpler_temps_forecast / kpler_generation_forecast. Passed as a list; the endpoint returns
# both in one request and echoes `model` per row.
_MODELS = ["EC_AIFS_ENS", "EC_46"]
# Kpler `loadType` enum is {demand, residual_demand}; we ingest total demand (matches the actual
# KP.LOAD.<zone> series). To also pull residual demand, loop this and tag sub_group/code per type.
_LOAD_TYPE = "demand"
# Re-pull the most recent few run dates each run, to catch revised runs and the late EC_46.
_REFRESH_DAYS = 3
# ponytail: bound the concurrent in-flight requests. `zones`/`models` batch into one request per
# run date, so a run is at most ~67 GETs on the first backfill (15 daily + 52 Mondays) and a
# handful incrementally — fan them out instead of looping serially, capped so we don't trip the
# rate limit (the shared helper still retries 429/5xx). Raise if the API tolerates more.
_CONCURRENCY = 8
# ponytail: no _HISTORY_START floor (unlike kpler_generation_forecast). EC_46 reaches back to
# ~2024, covering the whole trailing-year keep-set, so every vintage we request returns data
# (EC_AIFS_ENS only fills the recent run dates) — no pre-data Monday gets re-requested forever.


def _code(zone: str, model: str) -> str:
    """Series code, e.g. KP.LOADFC.FR.EC_46 / KP.LOADFC.DE.EC_AIFS_ENS."""
    return f"KP.LOADFC.{zone}.{model}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x model list -> one series per (zone, model)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], m),
            "name": f"{z['zone']} power demand forecast ({m})",
            "group": "demand_forecast",
            "sub_group": _LOAD_TYPE,
            "area": z["area"],
            "unit": "MW",
            "zone": z["zone"],  # used by to_canonical merge + fetch
            "model": m,  # used by to_canonical merge + fetch
        }
        for z in zones
        for m in _MODELS
    ]


def _desired_run_dates(today: dt.date) -> list[dt.date]:
    """This connector's fetch keep-set = the shared retention rule (no history floor)."""
    return desired_run_dates(today)


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
    """Delete forecast vintages outside the retention window (shared rule). Returns rows deleted."""
    return prune_vintages(session, source)


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
    refresh = {today - dt.timedelta(days=i) for i in range(_REFRESH_DAYS)}
    # clamp refresh to the keep-set: never re-fetch a vintage that just aged
    # out of the window (only for prune to delete it next run)
    run_dates = sorted((desired - have) | (desired & refresh))

    cols = ["zone", "model", "date", "value", "made_on"]
    if not run_dates:
        return pd.DataFrame(columns=cols)
    log.info(
        "kpler-load-fc: %d run-dates x %d zones x %d models, <=%d concurrent (today %s)",
        len(run_dates),
        len(zones),
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
) -> list[tuple[str, str, str, float, str]]:
    """Fan out one request per run date concurrently (bounded), collect raw rows.

    `zones` and `models` batch into a single request per run date (all zones + both models). The
    response echoes `zone`, `model` and `runDate`. A `_CONCURRENCY` semaphore caps in-flight
    requests; `arequest` handles 429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(rd: dt.date) -> list[tuple[str, str, str, float, str]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "runDate": rd.isoformat(),
                        "run": "00z",
                        "zones": zones,
                        "models": _MODELS,
                        "loadType": _LOAD_TYPE,
                        "granularity": "hourly",
                        "timezone": "UTC",
                    },
                    label="kpler-load-fc",
                )
            return [
                (d["zone"], d["model"], d["startDate"], d["value"], d["runDate"])
                for d in resp.json().get("data", [])
                if d.get("value") is not None
            ]

        chunks = await asyncio.gather(*(one(rd) for rd in run_dates))
    return [row for chunk in chunks for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map each (zone, model) hourly forecast to canonical rows, carrying `made_on`."""
    meta = pd.DataFrame(
        [
            {
                "zone": e["zone"],
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
    df = raw.merge(meta, on=["zone", "model"], how="inner")  # unknown zones/models drop out
    df["source"] = source
    cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source", "made_on"]
    return df[cols]
