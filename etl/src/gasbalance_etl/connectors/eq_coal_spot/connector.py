"""Energy Quantified coal connector — daily rolling **front-month** API-2 coal spot (USD/t).

Source: `GET /ohlc/{curve}/latest/?date=<D>` on the Energy Quantified (Montel EQ) OHLC API,
curve `Futures Coal API-2 USD/t ICE OHLC` — the ICE **API-2** future (the European CIF-ARA
coal benchmark). An exogenous **covariate** for gas-for-power demand: the coal price sets
gas-vs-coal switching economics, so it drives how much gas-fired generation runs. A single
global series, code `EQ.COAL.API2` (`group` = `price`, `sub_group` = `coal`).

"Rolling front month" = the contract with `period == "month"` and `front == 1` (each entry's
identity — `traded_at`/`period`/`front` — is nested under a `product` object; the prices sit at
the top level). Its daily spot value is the `settlement` price, falling back to `close` when
settlement isn't published yet (the current day, or a thin holiday). `/latest/?date=X` returns
the full contract list (every `front`/`period`) for the most recent trading day **<= X**, so we
key the canonical `date` on the response's own `traded_at` (NOT the requested date) and
`to_canonical` dedupes the prior-trading-day repeats that holidays produce.

The endpoint takes a single `date` per request, so a run is **one GET per weekday** in the
window (front-month coal doesn't trade weekends; a weekend request just repeats Friday), fanned
out concurrently (bounded by `_CONCURRENCY`) over the shared 429/5xx retry/backoff.

Storage: daily settlement → the single-vintage `covariate` table (midnight-UTC `ts`) via the
`load` hook — same sink as the other price covariates (ADR 0008).

Refresh: **incremental & self-managing**. `fetch()` reads the last loaded `covariate` timestamp
and pulls trading dates from there (minus a small overlap for late settlements); the first run
backfills from `_HISTORY_START`.

Auth: header `X-API-Key: <EQ_API_KEY>` (unlike the Basic-auth Kpler connectors).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_common import last_loaded_ts
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.eq_coal_spot.config import get_eq_settings
from gasbalance_etl.validation.coal_spot import coal_spot_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.eq_coal_spot.config import EqSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "eq_coal_spot"
schema = coal_spot_schema

# The EQ OHLC curve for ICE API-2 coal. Spaces and the "/" in "USD/t" must be percent-encoded
# (the "/" -> %2F so it stays part of the curve name, not a path separator); `quote(safe="")`
# reproduces the documented path exactly. Relative endpoint (no leading "/") so httpx joins it
# onto EQ_BASE_URL's "/api" prefix.
_CURVE = "Futures Coal API-2 USD/t ICE OHLC"
_ENDPOINT = f"ohlc/{quote(_CURVE, safe='')}/latest/"
_CODE = "EQ.COAL.API2"
# Rolling front month = the nearest monthly contract. EQ returns `period` lowercased ("month");
# `to_canonical` lowercases before comparing, so this stays robust to a casing change.
_PERIOD = "month"
_FRONT = 1
# Backfill start; earlier trading dates the curve doesn't cover return nothing and drop out.
_HISTORY_START = dt.date(2015, 1, 1)
# On incremental runs, re-pull this many trailing trading days so a late settlement overwrites a
# provisional close.
_REFRESH_DAYS = 5
# ponytail: bound the concurrent in-flight requests. A run is one GET per weekday in the window
# (a handful on an incremental run, ~2.8k on the first 2015-to-now backfill); fan them out
# instead of looping serially, with a cap so we don't trip the rate limit (the shared helper
# still retries 429/5xx). Raise if the API tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = the single coal front-month spot series (no per-zone list)."""
    return [
        {
            "code": _CODE,
            "name": "Coal API-2 front-month spot price (CIF ARA)",
            "group": "price",
            "sub_group": "coal",
            "area": "EU",
            "unit": "USD/t",
        }
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's daily settlement rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def _ohlc_list(payload: Any) -> list[dict[str, Any]]:
    """The OHLC entries out of a `/latest/` response, tolerant of the wrapper shape.

    EQ returns the contract list under a key (`data`) or, on some endpoints, as a bare list;
    handle both so the connector doesn't break on the wrapper. Anything else -> empty.
    """
    if isinstance(payload, list):
        return [o for o in payload if isinstance(o, dict)]
    if isinstance(payload, dict):
        for key in ("data", "ohlc", "items"):
            items = payload.get(key)
            if isinstance(items, list):
                return [o for o in items if isinstance(o, dict)]
    return []


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Incremental: pull the front-month OHLC for every trading day not yet loaded.

    `since` (framework contract) is ignored — the window is self-determined from the `covariate`
    table so a cron run is cheap and the first run backfills history. One `date` per request, so
    a run is one GET per weekday in the window, fanned out concurrently (bounded by
    `_CONCURRENCY`) over the shared 429/5xx retry.
    """
    del since
    cfg = get_eq_settings()
    last = last_loaded_ts(source)
    start = (
        _HISTORY_START
        if last is None
        else max(_HISTORY_START, last.date() - dt.timedelta(days=_REFRESH_DAYS))
    )
    today = dt.datetime.now(dt.UTC).date()  # everything is UTC, incl. the window boundary
    cols = ["traded", "period", "front", "settlement", "close"]
    if start > today:
        return pd.DataFrame(columns=cols)
    # Front-month coal doesn't trade on weekends; a weekend `/latest/` just repeats Friday, so
    # skip Sat/Sun (cuts ~28% of the first-backfill requests, loses nothing kept).
    # ponytail: drop the weekday() guard if the curve turns out to publish weekend dates.
    days = [
        d
        for i in range((today - start).days + 1)
        if (d := start + dt.timedelta(days=i)).weekday() < 5
    ]
    if not days:
        return pd.DataFrame(columns=cols)
    log.info(
        "eq_coal_spot: %d trading day(s), <=%d concurrent (%s..%s)",
        len(days),
        _CONCURRENCY,
        start,
        today,
    )

    rows = asyncio.run(_fetch_rows(cfg, days))

    return pd.DataFrame(rows, columns=cols)


async def _fetch_rows(cfg: EqSettings, days: list[dt.date]) -> list[dict[str, Any]]:
    """Fan out one `/latest/` request per trading date concurrently (bounded), collect raw rows.

    Order is irrelevant — `to_canonical` filters the front month by `(period, front)`. A
    `_CONCURRENCY` semaphore caps in-flight requests; `arequest` handles 429/5xx underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"X-API-Key": cfg.api_key, "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(day: dt.date) -> list[dict[str, Any]]:
            async with sem:
                resp = await arequest(client, _ENDPOINT, {"date": day.isoformat()}, label=source)
            rows: list[dict[str, Any]] = []
            for o in _ohlc_list(resp.json()):
                # Contract identity (traded_at/period/front) is nested under `product`; the
                # prices (settlement/close) sit at the entry's top level.
                prod = o.get("product")
                if not isinstance(prod, dict):
                    continue
                rows.append(
                    {
                        "traded": prod.get("traded_at"),
                        "period": prod.get("period"),
                        "front": prod.get("front"),
                        "settlement": o.get("settlement"),
                        "close": o.get("close"),
                    }
                )
            return rows

        results = await asyncio.gather(*(one(day) for day in days))
    return [row for chunk in results for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map the OHLC rows to canonical: keep the front month, settlement (else close), dedupe."""
    out_cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    meta = series_dict()[0]
    if raw.empty:
        return pd.DataFrame(columns=out_cols)
    period = raw["period"].astype(str).str.lower()  # EQ returns "month"; tolerate any casing
    df = raw[(period == _PERIOD) & (raw["front"] == _FRONT)].copy()
    # Settlement is the daily reference price; fall back to close when it isn't published yet.
    df["value"] = df["settlement"].where(df["settlement"].notna(), df["close"])
    df = df[df["value"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    # `traded` is a plain "YYYY-MM-DD"; canonical `date` is datetime64[ns] (naive UTC).
    df["date"] = pd.to_datetime(df["traded"])
    df["series_id"] = meta["code"]
    df["name"] = meta["name"]
    df["group"] = meta["group"]
    df["sub_group"] = meta["sub_group"]
    df["area"] = meta["area"]
    df["value"] = df["value"].astype(float)
    df["source"] = source
    # `/latest/` repeats the prior trading day around holidays -> dedupe (date, series_id), which
    # the canonical schema's unique constraint would otherwise reject.
    return df.drop_duplicates(subset=["date", "series_id"])[out_cols]
