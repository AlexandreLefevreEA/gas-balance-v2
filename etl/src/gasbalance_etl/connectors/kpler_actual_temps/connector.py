"""Kpler actual-temperature connector.

Source: `GET /power/loads/forecasts/temperature` (base forecast endpoint), hourly,
population-weighted 2 m temperature (°C) per power zone. Kpler has **no observed/
reanalysis** product, so we use the **day-ahead (D-1) slice** of each archived **00z
EC_OP** run as the best-estimate *actual* temperature: the run on day D forecasts
forward, and its slice for day D+1 is the 1-day-ahead value (a full 24 h, back to
~2018). The same-day (D-0) slice is only ~2 h before late-2025, so D-1 is the one
methodology that's consistent across the whole archive. (See ADR 0008.)

Auth: HTTP Basic with `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim).

Refresh: **incremental & self-managing**. The base endpoint is per-`runDate` (no
whole-range query before 2025-10-31), so we issue one request per run-day — but all
balance areas come back in that single request (`zones[]`). `fetch()` reads the last
loaded timestamp from `covariate` and pulls only from there (minus a small refresh
overlap); the first run, with an empty table, backfills from `_HISTORY_START` (~3 000
requests, a few minutes). Per-zone coverage varies — major Western-EU zones go back to
~2018, smaller/eastern ones start later; absent zones are simply skipped.

Each balance `area` maps 1:1 to a Kpler `zone` (`settings/kpler_actual_temps.yaml`).
Values are hourly, so this loads into `covariate` (not the daily `observation`) via the
`load` hook.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings, get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.kpler_actual_temps import temperature_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_actual_temps"
schema = temperature_schema

_ENDPOINT = "power/loads/forecasts/temperature"
# Base forecast archive starts ~spring 2018 (2018-03 empty, 2018-06 present). Earlier
# run-days just return empty and are filtered. Widen if Kpler backfills further.
_HISTORY_START = dt.date(2018, 4, 1)
# On incremental runs, re-pull this many trailing days to catch late-arriving revisions.
_REFRESH_DAYS = 5
# ponytail: 10-way concurrency clears the ~3 000-request first backfill in a few minutes;
# lower if Kpler rate-limits, raise if it tolerates and fetch is the bottleneck.
_MAX_CONCURRENCY = 10


def series_dict() -> list[dict[str, Any]]:
    return load_series_dict(source)


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's hourly rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def _zones() -> list[str]:
    return [e["zone"] for e in series_dict()]


def _day_ahead_rows(run_date: dt.date, data: list[dict[str, Any]]) -> list[tuple[str, str, float]]:
    """Keep the day-ahead (D-1) slice of a run: rows whose delivery day == run_date + 1.

    The 00z run on `run_date` forecasts forward; its next-calendar-day hours are the
    1-day-ahead horizon — our actual-temperature proxy. Drops nulls.
    """
    target = (run_date + dt.timedelta(days=1)).isoformat()
    return [
        (d["zone"], d["startDate"], d["value"])
        for d in data
        if d.get("value") is not None and str(d.get("startDate", ""))[:10] == target
    ]


def _last_loaded_day() -> dt.date | None:
    """Latest covariate timestamp already stored for this source, as a date (or None)."""
    from sqlalchemy import func, select

    from gasbalance_core.db import SessionLocal
    from gasbalance_core.models import Covariate, Series

    stmt = (
        select(func.max(Covariate.ts))
        .join(Series, Covariate.series_id == Series.id)
        .where(Series.source == source)
    )
    with SessionLocal() as session:
        ts = session.execute(stmt).scalar_one_or_none()
    return ts.date() if ts is not None else None


def _target_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """Inclusive list of delivery days [start, end] we want actual temperatures for."""
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


async def _fetch_all(
    cfg: KplerSettings, zones: list[str], run_dates: list[dt.date]
) -> pd.DataFrame:
    """One request per run-day (all zones at once) -> tidy long frame [zone, date, value]."""
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def _one(run_date: dt.date) -> list[tuple[str, str, float]]:
            async with sem:
                resp = await client.get(
                    _ENDPOINT,
                    params={
                        "runDate": run_date.isoformat(),
                        "run": "00z",
                        "zones": zones,
                        "models": ["EC_OP"],
                        "granularity": "hourly",
                        "timezone": "UTC",
                    },
                )
                resp.raise_for_status()
                return _day_ahead_rows(run_date, resp.json().get("data", []))

        results = await asyncio.gather(*[_one(rd) for rd in run_dates])

    flat = [r for sub in results for r in sub]
    df = pd.DataFrame(flat, columns=["zone", "date", "value"])
    if not df.empty:
        # Kpler returns tz-aware UTC ISO; canonical `date` is datetime64[ns] (naive UTC).
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Incremental: pull day-ahead actuals for every delivery day not yet loaded.

    `since` (framework contract) is ignored — the window is self-determined from the
    `covariate` table so a cron run is cheap and the first run backfills history.
    """
    del since
    cfg = get_kpler_settings()
    zones = _zones()
    last = _last_loaded_day()
    if last is None:
        start = _HISTORY_START
    else:
        start = max(_HISTORY_START, last - dt.timedelta(days=_REFRESH_DAYS))
    today = dt.datetime.now(dt.UTC).date()  # everything is UTC, incl. the window boundary
    if start > today:
        return pd.DataFrame(columns=["zone", "date", "value"])
    # To fill delivery day D, request the 00z run from D-1 (its day-ahead slice).
    run_dates = [d - dt.timedelta(days=1) for d in _target_days(start, today)]
    log.info(
        "kpler: %d run-days x %d zones (delivery days %s..%s)",
        len(run_dates),
        len(zones),
        start,
        today,
    )
    return asyncio.run(_fetch_all(cfg, zones, run_dates))


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map each zone's hourly temps to canonical rows via the area↔zone dictionary."""
    meta = pd.DataFrame(
        [
            {
                "zone": e["zone"],
                "series_id": e["code"],
                "name": e["name"],
                "group": e.get("group"),
                "sub_group": e.get("sub_group"),
                "area": e.get("area"),
            }
            for e in series_dict()
        ]
    )
    df = raw.merge(meta, on="zone", how="inner")  # unknown zones drop out
    df["source"] = source
    cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    return df[cols]
