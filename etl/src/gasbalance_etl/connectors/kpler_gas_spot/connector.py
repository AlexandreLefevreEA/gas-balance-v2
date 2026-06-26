"""Kpler gas day-ahead spot-price connector — daily settlement (EUR/MWh) per EU gas hub.

Source: `GET /power/prices/spot/gas`, the EEX gas spot prices. A **price covariate** for
gas-for-power demand: when the gas price (vs coal/carbon) is high, gas plants are the
marginal switch, so the hub day-ahead price helps explain gas burn. One series per gas
market area, code `KP.GASSPOT.<marketArea>`; `group = "price"`, `sub_group = "gas_spot"`.

The endpoint takes **one market area + one trading date per request** (params `marketArea`
and `tradingDate`; optional `provider` defaults to EEX). It returns several products per day
keyed by `tenor`; we keep the canonical **day-ahead "DAY 1 MW"** record — `tenor ==
"day_ahead"` whose `longName` ends "DAY 1 MW" (the next-gas-day delivery) — and take its
`settlementPrice`, falling back to `lastPrice` when settlement isn't published yet (the
current day, or a thin holiday). The within-day and weekend (SAT/SUN MW) legs and the
duplicate named spot-index root (`<HUB>DA`, same price) are dropped — see `_day1_value`.

Market areas: the 11 EUR/MWh hubs that publish a day-ahead price — TTF (NL), THE (DE),
PEG (FR), PVB (ES), CEGH (AT), OTE (CZ), ZTP (BE), FIN, LTU, LVA-EST, ETF
(`settings/kpler_gas_spot.yaml`). NBP is excluded (priced in p/therm, not EUR/MWh); GPL,
NCG and ZEE return no day-ahead and are excluded.

Storage: indexed by **trading date** (the price-discovery day — a daily series), it lands in
the `covariate` table via the `load` hook (ADR 0008); the EU gas-day alignment is applied
downstream in `ml/`, not here. Validated by `gas_spot_schema` (EUR/MWh band).

Refresh: **incremental & self-managing**. `fetch()` reads the last loaded `covariate`
timestamp and pulls from there (minus `_REFRESH_DAYS` overlap — which both absorbs the
trading-vs-delivery offset and lets a late settlement overwrite a provisional last-price);
the first run backfills from `_HISTORY_START` (TTF begins 2020; earlier or absent hub-days
return empty and drop). A run is `marketAreas x weekday tradingDates` GETs (continental
day-ahead doesn't trade on weekends), **fanned out concurrently** (bounded by `_CONCURRENCY`)
over the shared 429/5xx retry/backoff.

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
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.gas_spot import gas_spot_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_gas_spot"
schema = gas_spot_schema

_ENDPOINT = "power/prices/spot/gas"
# TTF day-ahead begins 2020-01 (2019 returns empty); other hubs start later and their earlier
# days simply return empty and drop out. Widen if Kpler backfills further.
_HISTORY_START = dt.date(2020, 1, 1)
# On incremental runs, re-pull this many trailing trading days: catches a late `settlementPrice`
# overwriting a provisional `lastPrice`, and covers the trading-vs-delivery day offset.
_REFRESH_DAYS = 5
# ponytail: bound the concurrent in-flight requests. A run is marketAreas x tradingDates GETs
# (a handful on an incremental run, ~18k on the first multi-year backfill); fan them out instead
# of looping serially, with a cap so we don't trip the rate limit (the shared helper still
# retries 429/5xx). Raise if the API tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def _code(market_area: str) -> str:
    """Series code, e.g. KP.GASSPOT.TTF / KP.GASSPOT.THE."""
    return f"KP.GASSPOT.{market_area}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = market-area YAML -> one day-ahead spot series per hub."""
    areas = load_series_dict(source)  # [{area, market_area}, ...]
    return [
        {
            "code": _code(e["market_area"]),
            "name": f"{e['market_area']} gas day-ahead spot price",
            "group": "price",
            "sub_group": "gas_spot",
            "area": e["area"],
            "unit": "EUR/MWh",
            "market_area": e["market_area"],  # used by to_canonical merge + fetch
        }
        for e in areas
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's daily rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def _day1_value(records: list[dict[str, Any]]) -> float | None:
    """The day-ahead 'DAY 1 MW' settlement (fallback last trade) — the canonical daily spot.

    Among the day's products, keep the `tenor == "day_ahead"` record whose `longName` ends
    "DAY 1 MW" (the next-gas-day delivery — not the SAT/SUN MW weekend legs, the within-day,
    or the duplicate named spot-index root `<HUB>DA`). Prefer `settlementPrice`; fall back to
    `lastPrice` when settlement isn't published yet. Returns None if there's no such record
    or it carries no price.
    """
    for r in records:
        long_name = str(r.get("longName", "")).upper()
        if r.get("tenor") == "day_ahead" and long_name.endswith("DAY 1 MW"):
            price = r.get("settlementPrice")
            if price is None:
                price = r.get("lastPrice")
            return None if price is None else float(price)
    return None


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Incremental: pull the day-ahead spot for every trading day not yet loaded.

    `since` (framework contract) is ignored — the window is self-determined from the
    `covariate` table so a cron run is cheap and the first run backfills history. The endpoint
    takes one market area + one trading date per request, so a run is `marketAreas x trading
    days` GETs, **fanned out concurrently** (bounded by `_CONCURRENCY`) over the shared retry.
    """
    del since
    cfg = get_kpler_settings()
    market_areas = [e["market_area"] for e in load_series_dict(source)]
    last = last_loaded_ts(source)
    start = (
        _HISTORY_START
        if last is None
        else max(_HISTORY_START, last.date() - dt.timedelta(days=_REFRESH_DAYS))
    )
    today = dt.datetime.now(dt.UTC).date()  # everything is UTC, incl. the window boundary
    cols = ["market_area", "date", "value"]
    if start > today:
        return pd.DataFrame(columns=cols)
    # Continental day-ahead gas doesn't trade on weekends, so weekend tradingDates just return
    # empty — skip them (cuts ~28% of the first-backfill requests, loses nothing kept).
    # ponytail: drop the weekday() guard if a hub turns out to trade weekends.
    trading_dates = [
        d
        for i in range((today - start).days + 1)
        if (d := start + dt.timedelta(days=i)).weekday() < 5
    ]
    if not trading_dates:
        return pd.DataFrame(columns=cols)
    log.info(
        "kpler_gas_spot: %d market areas x %d trading days, <=%d concurrent (%s..%s)",
        len(market_areas),
        len(trading_dates),
        _CONCURRENCY,
        start,
        today,
    )

    rows = asyncio.run(_fetch_rows(cfg, market_areas, trading_dates))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])  # 'YYYY-MM-DD' trading date -> naive datetime64
    return df[cols]


async def _fetch_rows(
    cfg: KplerSettings,
    market_areas: list[str],
    trading_dates: list[dt.date],
) -> list[dict[str, Any]]:
    """Fan out one request per (market area, trading date) concurrently (bounded), collect rows.

    Each request is one market area over one trading day; order is irrelevant (`to_canonical`
    merges on `market_area`). A `_CONCURRENCY` semaphore caps in-flight requests; `arequest`
    handles 429/5xx retries underneath. `value` may be None (no day-ahead / unsettled) — it
    drops out in `to_canonical`.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)
    combos = [(ma, td) for ma in market_areas for td in trading_dates]

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(market_area: str, td: dt.date) -> dict[str, Any]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {"marketArea": market_area, "tradingDate": td.isoformat()},
                    label="kpler_gas_spot",
                )
            return {
                "market_area": market_area,
                "date": td.isoformat(),
                "value": _day1_value(resp.json().get("data", [])),
            }

        return list(await asyncio.gather(*(one(ma, td) for ma, td in combos)))


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map (market area, trading day) spot rows to canonical series; drop nulls and unknown hubs."""
    out_cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    meta = pd.DataFrame(
        [
            {
                "market_area": e["market_area"],
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
    df = df.merge(meta, on="market_area", how="inner")  # unknown market areas drop out
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["value"] = df["value"].astype(float)
    df["source"] = source
    return df[out_cols]
