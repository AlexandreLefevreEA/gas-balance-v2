"""Kpler day-ahead power-price connector — hourly electricity spot price (EUR/MWh) per zone.

Source: `GET /power/prices/day-ahead`, hourly day-ahead auction prices (EUR/MWh) per power
zone. An exogenous **covariate** for gas-for-power demand: when power prices are high, more
gas plants are in the money and run, lifting gas demand. The price-side sibling of the actual
covariates `kpler_power_demand` (load) and `kpler_generation_actual` (generation). One series
per zone, code `KP.SPOT.<zone>`; `sub_group` = `day_ahead`.

Storage: values are **hourly**, so this loads into the `covariate` table (not the daily
`observation`) via the `load` hook — same as the other actual covariates (ADR 0008). We store
the raw hourly UTC series; the EU **gas-day (06:00 CET, DST-aware)** aggregation is applied
downstream in `ml/`, not here.

Refresh: **incremental & self-managing**. `fetch()` reads the last loaded `covariate`
timestamp and pulls from there (minus a small overlap for late-published recent hours); the
first run backfills from `_HISTORY_START`. The endpoint **batches all zones in one request**
(unlike `kpler_power_demand`'s singular `zone`), so a run is **one request per date chunk**,
fanned out concurrently (bounded by `_CONCURRENCY`) over the shared 429/5xx retry/backoff.

Zones: `zones` is the ENTSO-E **bidding-zone** enum (like `kpler_generation_forecast`, NOT the
loads/country-code family) — Germany is `DE-LU` (not `DE`), and split countries take a
sub-zone: Denmark `DK1`, Italy `IT-NORTH` (Italy's national `IT-PUN` is in the enum but
returns no data). The area->zone map is explicit in the YAML so these quirks are remapped
without touching code. GB prices are normalised to EUR by Kpler, so there is no currency split.

Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler connectors).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_common import date_chunks, last_loaded_ts
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.spot_price import spot_price_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_power_spot"
schema = spot_price_schema

_ENDPOINT = "power/prices/day-ahead"
_SUB_GROUP = "day_ahead"
# First populated day: the API hard-floors startDate at 2014-01-01, 2015 returns empty, and
# 2016 is the first day with data; earlier requests just drop out.
_HISTORY_START = dt.date(2016, 1, 1)
# On incremental runs, re-pull this many trailing days to catch late-published recent hours.
_REFRESH_DAYS = 5
# Chunk the backfill window; the endpoint batches all zones per request, so a chunk is one GET.
# Only the first backfill (2016..now) needs >1 chunk; incremental runs span days.
_CHUNK_DAYS = 365
# ponytail: bound the concurrent in-flight requests. A run is one GET per date chunk (1 on an
# incremental run, ~11 on the first backfill); fan them out instead of looping serially, with a
# cap so we don't trip the rate limit (the shared helper still retries 429/5xx). Raise if the
# API tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def _code(zone: str) -> str:
    """Series code, e.g. KP.SPOT.FR / KP.SPOT.DE-LU / KP.SPOT.IT-NORTH."""
    return f"KP.SPOT.{zone}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML -> one day-ahead price series per zone."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"]),
            "name": f"{z['zone']} day-ahead power price",
            "group": "price",
            "sub_group": _SUB_GROUP,
            "area": z["area"],
            "unit": "EUR/MWh",
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


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Incremental: pull hourly day-ahead prices for every hour not yet loaded.

    `since` (framework contract) is ignored — the window is self-determined from the
    `covariate` table so a cron run is cheap and the first run backfills history. The endpoint
    batches all zones in one request, so a run is one request per date chunk, fanned out
    concurrently (bounded by `_CONCURRENCY`) over the shared 429/5xx retry.
    """
    del since
    cfg = get_kpler_settings()
    zones = [e["zone"] for e in load_series_dict(source)]
    last = last_loaded_ts(source)
    start = (
        _HISTORY_START
        if last is None
        else max(_HISTORY_START, last.date() - dt.timedelta(days=_REFRESH_DAYS))
    )
    end = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)  # endDate exclusive; include today
    cols = ["zone", "date", "value"]
    if start >= end:
        return pd.DataFrame(columns=cols)
    chunks = date_chunks(start, end, _CHUNK_DAYS)
    log.info(
        "kpler_power_spot: %d zones x %d chunk(s), <=%d concurrent (%s..%s)",
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
    """Fan out one request per date chunk concurrently (bounded), collect raw rows.

    `zones` batches every zone into a single request, so each request is the whole zone set over
    one date chunk. Order is irrelevant — `to_canonical` merges on zone. A `_CONCURRENCY`
    semaphore caps in-flight requests; `arequest` handles 429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(lo: dt.date, hi: dt.date) -> list[dict[str, Any]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "zones": zones,  # plural: all zones batched in one request
                        "granularity": "hourly",
                        "timezone": "UTC",
                        "startDate": lo.isoformat(),
                        "endDate": hi.isoformat(),
                    },
                    label="kpler_power_spot",
                )
            # Very recent hours can omit `value`; None drops out in to_canonical.
            return [
                {"zone": d["zone"], "date": d["startDate"], "value": d.get("value")}
                for d in resp.json().get("data", [])
            ]

        results = await asyncio.gather(*(one(lo, hi) for lo, hi in chunks))
    return [row for chunk in results for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map (zone, hour) price rows to canonical series; drop nulls and unknown zones."""
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
