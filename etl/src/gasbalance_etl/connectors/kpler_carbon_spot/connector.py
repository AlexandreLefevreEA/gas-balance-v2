"""Kpler carbon-spot connector — daily EU carbon (EUA) emissions spot price (EUR/tCO2).

Source: `GET /power/prices/spot/emissions`, the EU Emissions Allowance (EUA) spot settlement
price. One exogenous **covariate** for gas-for-power demand: the carbon price sets gas-vs-coal
switching economics, so it drives how much gas-fired generation runs. A single global EU series,
code `KP.CARBON.SPOT`.

The endpoint is unlike the loads/generation ones: its only params are `tradingDate` (a single
date, required) and `provider` (default `eex`) — **no zone, no date range, no granularity** — so a
run is **one request per trading date**. Each day returns the emissions products traded; we keep
`root == "SEME"` ("EEX EUA Spot", the EU Allowance — present every trading day) and take its
`settlementPrice`. `root == "SEMA"` is the EUAA *aviation* allowance (zero volume) and is dropped.
Non-trading days (weekends/holidays) return an empty list.

Storage: daily settlement → the single-vintage `covariate` table, keyed by a midnight-UTC `ts`,
via the `load` hook — same sink as the other Kpler actual covariates (ADR 0008).

Refresh: **incremental & self-managing**. `fetch()` reads the last loaded `covariate` timestamp
and pulls trading dates from there (minus a small overlap for settlement revisions); the first run
backfills from `_HISTORY_START` (data begins ~2015; earlier dates return empty). Requests are
**fanned out concurrently** (bounded by `_CONCURRENCY`) over the shared 429/5xx retry/backoff.

Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler connectors).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_common import last_loaded_ts
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.validation.carbon import carbon_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_carbon_spot"
schema = carbon_schema

_ENDPOINT = "power/prices/spot/emissions"
# Pin the provider — it is the default, but be explicit so a default change can't shift the series.
_PROVIDER = "eex"
# The endpoint returns two product roots per day: SEME = "EEX EUA Spot" (the EU Allowance carbon
# price, present every trading day) and SEMA = the EUAA *aviation* allowance (zero volume). We keep
# SEME and take its settlement price.
_ROOT = "SEME"
_CODE = "KP.CARBON.SPOT"
# Data begins ~2015 (2014 returns empty); earlier trading dates return empty and drop out.
_HISTORY_START = dt.date(2015, 1, 1)
# On incremental runs, re-pull this many trailing days to catch late settlement revisions.
_REFRESH_DAYS = 5
# ponytail: bound the concurrent in-flight requests. The endpoint takes one trading date per
# request, so a run is one GET per calendar day in the window (a handful on an incremental run,
# ~4k on the first 2015-to-now backfill); fan them out instead of looping serially, with a cap so
# we don't trip the rate limit (the shared helper still retries 429/5xx). Raise if the API
# tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = the single EU carbon-spot series (no per-zone list to externalise)."""
    return [
        {
            "code": _CODE,
            "name": "EU carbon emissions spot price (EUA)",
            "group": "carbon",
            "sub_group": "eua",
            "area": "EU",
            "unit": "EUR/tCO2",
        }
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's daily settlement rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Incremental: pull the EUA spot settlement for every trading date not yet loaded.

    `since` (framework contract) is ignored — the window is self-determined from the `covariate`
    table so a cron run is cheap and the first run backfills history. The endpoint takes one
    trading date per request (no zone, no range), so a run is one GET per calendar day in the
    window, fanned out concurrently (bounded by `_CONCURRENCY`) over the shared 429/5xx retry.
    """
    del since
    cfg = get_kpler_settings()
    last = last_loaded_ts(source)
    start = (
        _HISTORY_START
        if last is None
        else max(_HISTORY_START, last.date() - dt.timedelta(days=_REFRESH_DAYS))
    )
    end = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)  # include today
    cols = ["date", "root", "value"]
    if start >= end:
        return pd.DataFrame(columns=cols)
    dates = [start + dt.timedelta(days=i) for i in range((end - start).days)]
    log.info(
        "kpler_carbon_spot: %d trading date(s), <=%d concurrent (%s..%s)",
        len(dates),
        _CONCURRENCY,
        start,
        end,
    )

    rows = asyncio.run(_fetch_rows(cfg, dates))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # `tradingDate` is a plain "YYYY-MM-DD"; canonical `date` is datetime64[ns] (naive UTC).
        df["date"] = pd.to_datetime(df["date"])
    return df[cols]


async def _fetch_rows(cfg: KplerSettings, dates: list[dt.date]) -> list[dict[str, Any]]:
    """Fan out one request per trading date concurrently (bounded), collect raw rows.

    Order is irrelevant — `to_canonical` filters by root. A `_CONCURRENCY` semaphore caps in-flight
    requests; `arequest` handles 429/5xx retries underneath. Non-trading days return an empty list.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(day: dt.date) -> list[dict[str, Any]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {"tradingDate": day.isoformat(), "provider": _PROVIDER},
                    label="kpler_carbon_spot",
                )
            return [
                {"date": d["tradingDate"], "root": d.get("root"), "value": d.get("settlementPrice")}
                for d in resp.json().get("data", [])
            ]

        results = await asyncio.gather(*(one(day) for day in dates))
    return [row for chunk in results for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map trading-date rows to canonical: keep the SEME (EUA spot) settlement, drop nulls."""
    out_cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    meta = series_dict()[0]
    df = raw[raw["root"] == _ROOT]
    df = df[df["value"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["series_id"] = meta["code"]
    df["name"] = meta["name"]
    df["group"] = meta["group"]
    df["sub_group"] = meta["sub_group"]
    df["area"] = meta["area"]
    df["value"] = df["value"].astype(float)
    df["source"] = source
    # Older history repeats a trading day's SEME row (a holiday echoes the prior day), so dedupe
    # (date, series_id) — same guard as eq_coal_spot; the schema's unique key is otherwise tripped.
    return df.drop_duplicates(subset=["date", "series_id"])[out_cols]
