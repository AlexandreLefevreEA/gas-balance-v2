"""Kpler gas-forward-curve connector — daily forward gas prices, a forecast covariate.

Source: `GET /power/prices/price-forward-curve/gas`, the daily **forward price curve** for EU gas
hubs per trading date (EUR/MWh for the continental hubs; NBP in GBX/thm). One value per delivery
day; the curve runs several years out. One series per hub (the `marketAreas` enum), code
`KP.GASFC.<hub>`; `sub_group` holds the hub's quote currency.

**The vintage dimension.** A forward curve is `(tradingDate, deliveryDate) -> price`: the same
delivery day is re-priced on every trading date. So values land in `forecast_covariate`, keyed by
`(series_id, made_on, ts)` (`made_on` = the trading date), not the single-vintage `covariate`.
See ADR 0009.

**Retention.** Same rule as the other `*_forecast` connectors (and re-runnable via
`etl prune kpler_gas_forward_curve`): keep **all** trading dates from the last 15 days, plus
**every Monday** for 1 year; delete the rest.

**Refresh: self-managing, and backfills missing vintages.** Each run computes the desired keep-set
of trading dates, subtracts what's already stored, and fetches every missing vintage plus a small
recent overlap re-pulled for any late revision. The keep-set is **filtered to weekdays** — gas
markets don't trade on weekends, so a weekend `tradingDate` returns no curve (requesting it every
run would fetch empty forever). The curve history begins ~early 2025, which fully covers the
trailing-year keep-set, so there is **no `_HISTORY_START` floor** (like
`kpler_power_demand_forecast`).

**Request grain.** `marketAreas` batches every hub into one request, so fetch issues **one request
per trading date** — fanned out concurrently (bounded by `_CONCURRENCY`) over the shared 429/5xx
retry. Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler connectors).
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
from gasbalance_etl.validation.forecast_covariate import forecast_covariate_gas_price_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_gas_forward_curve"
schema = forecast_covariate_gas_price_schema

_ENDPOINT = "power/prices/price-forward-curve/gas"
# Re-pull the most recent few trading dates each run, to catch a late revision of a curve.
_REFRESH_DAYS = 3
# ponytail: bound the concurrent in-flight requests. `marketAreas` batches every hub into one
# request per trading date, so a run is at most ~63 GETs on the first backfill (the weekday
# keep-set: ~11 recent weekdays + ~52 Mondays) and a handful incrementally — fan them out instead
# of looping serially, capped so we don't trip the rate limit (the shared helper retries 429/5xx).
_CONCURRENCY = 8
# ponytail: no _HISTORY_START floor (unlike kpler_generation_forecast). The curve history begins
# ~early 2025, covering the whole trailing-year keep-set, so every weekday vintage we request
# returns data — no pre-data Monday gets re-requested forever.


def _code(hub: str) -> str:
    """Series code, e.g. KP.GASFC.TTF / KP.GASFC.NBP."""
    return f"KP.GASFC.{hub}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = hub YAML -> one series per hub (no model dimension)."""
    hubs = load_series_dict(source)  # [{hub, currency}, ...]
    return [
        {
            "code": _code(h["hub"]),
            "name": f"{h['hub']} gas forward curve",
            "group": "gas_forward_curve",
            "sub_group": h["currency"],  # EUR/MWh, or GBX/thm for NBP
            "area": h["hub"],
            "unit": h["currency"],
            "hub": h["hub"],  # used by to_canonical merge + fetch
        }
        for h in hubs
    ]


def _desired_run_dates(today: dt.date) -> list[dt.date]:
    """Fetch keep-set = the shared retention rule, minus weekends (no curve on weekends)."""
    return [d for d in desired_run_dates(today) if d.weekday() < 5]


def _vintages_to_delete(made_ons: list[dt.date], today: dt.date) -> set[dt.date]:
    """This connector's retention rule (shared, pure); `prune` wraps it in SQL."""
    return vintages_to_delete(made_ons, today)


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route forward-curve rows to `forecast_covariate`, then enforce retention.

    The prune runs in the same transaction (the CLI commits on success), so a failed load rolls
    back both. Imported lazily so importing the connector (for the registry) is DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_forecast_covariates

    written = upsert_forecast_covariates(session, df, run_id, code_to_id)
    prune(session)
    return written


def prune(session: Session) -> int:
    """Delete forecast vintages outside the retention window (shared rule). Returns rows deleted."""
    return prune_vintages(session, source)


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Fetch every desired forward-curve vintage not already stored (+ a recent refresh overlap).

    `since` (framework contract) is ignored — the window is the retention keep-set,
    self-determined from what `forecast_covariate` already holds, so gaps are backfilled.
    """
    del since
    cfg = get_kpler_settings()
    hubs = [e["hub"] for e in series_dict()]
    today = dt.datetime.now(dt.UTC).date()

    desired = set(_desired_run_dates(today))
    have = loaded_run_dates(source)
    refresh = {today - dt.timedelta(days=i) for i in range(_REFRESH_DAYS)}
    # clamp refresh to the keep-set: never re-fetch a vintage that just aged out of the window
    # (only for prune to delete it next run), nor a weekend (not in `desired`).
    run_dates = sorted((desired - have) | (desired & refresh))

    cols = ["zone", "date", "value", "made_on"]
    if not run_dates:
        return pd.DataFrame(columns=cols)
    log.info(
        "kpler-gas-fwd: %d trading-dates x %d hubs, <=%d concurrent (today %s)",
        len(run_dates),
        len(hubs),
        _CONCURRENCY,
        today,
    )

    rows = asyncio.run(_fetch_rows(cfg, run_dates, hubs))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # Kpler returns date-only delivery days ('YYYY-MM-DD'); canonical `date` is naive.
        df["date"] = pd.to_datetime(df["date"])
        df["made_on"] = pd.to_datetime(df["made_on"])  # 'YYYY-MM-DD' -> naive datetime64
    return df


async def _fetch_rows(
    cfg: KplerSettings, run_dates: list[dt.date], hubs: list[str]
) -> list[tuple[str, str, float, str]]:
    """Fan out one request per trading date concurrently (bounded), collect raw rows.

    `marketAreas` batches every hub into a single request per trading date; the response echoes
    `zone` (the hub) and `tradingDate`. A `_CONCURRENCY` semaphore caps in-flight requests;
    `arequest` handles 429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(rd: dt.date) -> list[tuple[str, str, float, str]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {"tradingDate": rd.isoformat(), "marketAreas": hubs},
                    label="kpler-gas-fwd",
                )
            return [
                (d["zone"], d["startDate"], d["value"], d["tradingDate"])
                for d in resp.json().get("data", [])
                if d.get("value") is not None
            ]

        chunks = await asyncio.gather(*(one(rd) for rd in run_dates))
    return [row for chunk in chunks for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map each hub's daily forward price to canonical rows, carrying `made_on`."""
    meta = pd.DataFrame(
        [
            {
                "hub": e["hub"],
                "series_id": e["code"],
                "name": e["name"],
                "group": e["group"],
                "sub_group": e["sub_group"],
                "area": e["area"],
            }
            for e in series_dict()
        ]
    )
    df = raw.merge(meta, left_on="zone", right_on="hub", how="inner")  # unknown hubs drop out
    df["source"] = source
    cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source", "made_on"]
    return df[cols]
