"""Kpler actual power-demand connector — hourly electricity load (MW) per power zone.

Source: `GET /power/loads/actual`, hourly actual demand (total system load, MW) per power
zone. One exogenous **covariate** per zone for gas-for-power demand: when electricity load
is high, more gas plants run. One series per zone, code `KP.LOAD.<zone>`; `sub_group` holds
the Kpler `loadType` (`demand` = total load; `residual_demand` = load net of renewables is a
one-line add — see `_LOAD_TYPE`).

Storage: values are **hourly**, so this loads into the `covariate` table (not the daily
`observation`) via the `load` hook — same as `kpler_generation_actual` (ADR 0008). We store
the raw hourly UTC series; the EU **gas-day (06:00 CET, DST-aware)** aggregation is applied
downstream in `ml/`, not here.

Refresh: **incremental & self-managing**. `fetch()` reads the last loaded `covariate`
timestamp and pulls from there (minus a small overlap for revisions); the first run
backfills from `_HISTORY_START`. Unlike the generation endpoint, `/power/loads/actual` takes
**one zone per request** (`zone` is singular) and caps a request at a **12-year range**, so a
run is `zones x date-chunks` requests (each chunk well under the cap) — **fanned out
concurrently** (bounded by `_CONCURRENCY`) over the shared 429/5xx retry/backoff.

Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler connectors).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.demand import demand_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_power_demand"
schema = demand_schema

_ENDPOINT = "power/loads/actual"
# Kpler `loadType` enum is {demand, residual_demand}; we ingest total demand. To also pull
# residual demand (load net of renewables), loop this and tag `sub_group`/code per type.
_LOAD_TYPE = "demand"
# ENTSO-E-era start; earlier days return empty and drop out (FR reaches 2014, most ~2016).
_HISTORY_START = dt.date(2015, 1, 1)
# On incremental runs, re-pull this many trailing days to catch late-arriving revisions.
_REFRESH_DAYS = 5
# The endpoint rejects a request whose range exceeds 12 years; keep chunks safely under it.
# One zone-hour-decade is ~88k uncapped rows, so a ~10-year chunk is fine (and rare: only
# the first backfill needs >1 chunk; incremental runs span days).
_CHUNK_DAYS = 3650
# ponytail: bound the concurrent in-flight requests. A run is zones x chunks GETs (18 on an
# incremental run, ~36 on the first backfill); fan them out instead of looping serially, with
# a cap so we don't trip the rate limit (the shared helper still retries 429/5xx). Raise if the
# API tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def _code(zone: str) -> str:
    """Series code, e.g. KP.LOAD.FR / KP.LOAD.DE."""
    return f"KP.LOAD.{zone}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML -> one demand series per zone."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"]),
            "name": f"{z['zone']} power demand",
            "group": "demand",
            "sub_group": _LOAD_TYPE,
            "area": z["area"],
            "unit": "MW",
            "zone": z["zone"],  # used by to_canonical merge + fetch
        }
        for z in zones
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's hourly rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def _last_loaded_ts() -> dt.datetime | None:
    """Latest covariate timestamp already stored for this source (or None)."""
    from sqlalchemy import func, select

    from gasbalance_core.db import SessionLocal
    from gasbalance_core.models import Covariate, Series

    stmt = (
        select(func.max(Covariate.ts))
        .join(Series, Covariate.series_id == Series.id)
        .where(Series.source == source)
    )
    with SessionLocal() as session:
        return session.execute(stmt).scalar_one_or_none()


def _date_chunks(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Split [start, end) into <= _CHUNK_DAYS windows (the API's endDate is exclusive)."""
    chunks: list[tuple[dt.date, dt.date]] = []
    cur = start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=_CHUNK_DAYS), end)
        chunks.append((cur, nxt))
        cur = nxt
    return chunks


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Incremental: pull hourly demand for every hour not yet loaded, one zone per request.

    `since` (framework contract) is ignored — the window is self-determined from the
    `covariate` table so a cron run is cheap and the first run backfills history. The endpoint
    takes one zone per request (and caps the range at 12 years), so a run is `zones x chunks`
    GETs, fanned out concurrently (bounded by `_CONCURRENCY`) over the shared 429/5xx retry.
    """
    del since
    cfg = get_kpler_settings()
    zones = [e["zone"] for e in load_series_dict(source)]
    last = _last_loaded_ts()
    start = (
        _HISTORY_START
        if last is None
        else max(_HISTORY_START, last.date() - dt.timedelta(days=_REFRESH_DAYS))
    )
    end = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)  # endDate exclusive; include today
    cols = ["zone", "date", "value"]
    if start >= end:
        return pd.DataFrame(columns=cols)
    chunks = _date_chunks(start, end)
    log.info(
        "kpler_power_demand: %d zones x %d chunk(s), <=%d concurrent (%s..%s)",
        len(zones),
        len(chunks),
        _CONCURRENCY,
        start,
        end,
    )

    rows = asyncio.run(_fetch_rows(cfg, zones, chunks))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # Kpler returns tz-aware UTC ISO; canonical `date` is datetime64[ns] (naive UTC).
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df[cols]


async def _fetch_rows(
    cfg: KplerSettings,
    zones: list[str],
    chunks: list[tuple[dt.date, dt.date]],
) -> list[dict[str, Any]]:
    """Fan out one request per (zone, chunk) concurrently (bounded), collect raw rows.

    `zone` is singular on this endpoint, so each request is one zone over one date chunk. Order
    is irrelevant — `to_canonical` merges on zone. A `_CONCURRENCY` semaphore caps in-flight
    requests; `arequest` handles 429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)
    combos = [(zone, lo, hi) for zone in zones for lo, hi in chunks]

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(zone: str, lo: dt.date, hi: dt.date) -> list[dict[str, Any]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "zone": zone,  # singular: one zone per request
                        "loadType": _LOAD_TYPE,
                        "granularity": "hourly",
                        "timezone": "UTC",
                        "startDate": lo.isoformat(),
                        "endDate": hi.isoformat(),
                    },
                    label="kpler_power_demand",
                )
            # Very recent hours can omit `value`; None drops out in to_canonical.
            return [
                {"zone": zone, "date": d["startDate"], "value": d.get("value")}
                for d in resp.json().get("data", [])
            ]

        results = await asyncio.gather(*(one(z, lo, hi) for z, lo, hi in combos))
    return [row for chunk in results for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map (zone, hour) demand rows to canonical series; drop nulls and unknown zones."""
    out_cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    meta = pd.DataFrame(
        [
            {
                "zone": e["zone"],
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
    df = df[df["value"].notna()]
    df = df.merge(meta, on="zone", how="inner")  # unknown zones drop out
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["value"] = df["value"].astype(float)
    df["source"] = source
    return df[out_cols]
