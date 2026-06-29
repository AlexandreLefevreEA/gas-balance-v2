"""Kpler carbon-settles connector — EU carbon (EUA) futures settlement anchors (EUR/tCO2).

Source: `GET /power/prices/futures/settlements/emissions`, the EEX emissions futures
**settlement** prices of a given trading date. We keep the **EU Allowance (EUA, "ETS1")**
monthly contracts and store their settlement points as the raw forward-curve anchors that the
`carbon_curve` transform later splines into a daily curve. A single EU series,
code `KP.CARBON.SETTLES`.

**The vintage dimension.** Like a forward curve, this is `(tradingDate, maturityDate) → value`:
each trading day re-settles the whole strip, so the same contract recurs in every trading date's
strip. Rows land in `forecast_covariate`, keyed `(series_id, made_on, ts)` (`made_on` = the
**trading date**, `ts` = the contract **maturity date**), not the single-vintage `covariate`. See
ADR 0009. (`kpler_carbon_spot` is the *spot* analogue, a single-vintage covariate.)

**Retention.** Multi-vintage storage is bounded by the shared rule enforced after every load (and
re-runnable via `etl prune kpler_carbon_settles`): keep **all** trading dates from the last 15
days, plus **every Monday** for 1 year; delete the rest — same rule as the other `*_forecast`
connectors and `kpler_power_forward_curve`.

**Refresh: self-managing, backfills missing vintages.** Each run computes the desired keep-set of
trading dates, subtracts what's already stored, and fetches every missing vintage plus a small
recent overlap re-pulled for revised settlements.

**Request grain.** The endpoint takes one `tradingDate` per request (no zone/range), so a run is
**one request per trading date** — fanned out concurrently (bounded by `_CONCURRENCY`) over the
shared 429/5xx retry. Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler
connectors).

**Endpoint quirks** (probed live):
- Params: `tradingDate` (required) + `provider` (default `eex`).
- Each trading date returns **three** product families, all `maturityType == "month"`:
  `EEX EUA Future` (EUR/tCO2, the EU ETS1 allowance — **what we keep**), `EEX EU ETS2 Future`
  (EUR/EUA2, the new buildings/transport scheme) and `EEX UKA Futures` (GBP/UKA, UK allowance).
  We filter on `longName == "EEX EUA Future"`.
- `maturityDate` is the 1st of the contract month; the front contract can predate `tradingDate`.
  We store every EUA anchor as-is — the `carbon_curve` transform drops past maturities when it
  builds the spline (the spot is the near anchor there).
- Transient `502`/`429` are handled by the shared `arequest` retry/backoff.
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
from gasbalance_etl.validation.forecast_covariate import forecast_covariate_carbon_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_carbon_settles"
schema = forecast_covariate_carbon_schema

_ENDPOINT = "power/prices/futures/settlements/emissions"
# Pin the provider — it is the default, but be explicit so a default change can't shift the series.
_PROVIDER = "eex"
# Keep the EU Allowance (ETS1) monthly contracts only. `longName` discriminates the three product
# families the endpoint returns: "EEX EUA Future" (EUR/tCO2, ours), "EEX EU ETS2 Future" (EUR/EUA2)
# and "EEX UKA Futures" (GBP/UKA). All are `maturityType == "month"`.
_LONG_NAME = "EEX EUA Future"
_MATURITY_TYPE = "month"
_CODE = "KP.CARBON.SETTLES"
# Re-pull the most recent few trading dates each run, to catch revised settlements.
_REFRESH_DAYS = 3
# ponytail: bound the concurrent in-flight requests. One GET per trading date, so a run is at most
# ~67 GETs on the first backfill (15 daily + 52 Mondays) and a handful incrementally — fan them out
# instead of looping serially, capped so we don't trip the rate limit (the shared helper still
# retries 429/5xx). Raise if the API tolerates more.
_CONCURRENCY = 8
# ponytail: no _HISTORY_START floor. EUA futures history is deep (anchors returned for 2023→now),
# so the trailing-year Monday keep-set is fully covered; weekend/holiday trading dates return 0 rows
# and get harmlessly re-requested each run.


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = the single EU carbon futures-settlement series (no per-zone list)."""
    return [
        {
            "code": _CODE,
            "name": "EU carbon (EUA) futures settlement curve",
            "group": "carbon",
            "sub_group": "eua_settles",
            "area": "EU",
            "unit": "EUR/tCO2",
        }
    ]


def _desired_run_dates(today: dt.date) -> list[dt.date]:
    """This connector's fetch keep-set = the shared retention rule (no history floor)."""
    return desired_run_dates(today)


def _vintages_to_delete(made_ons: list[dt.date], today: dt.date) -> set[dt.date]:
    """This connector's retention rule (shared, pure); `prune` wraps it in SQL."""
    return vintages_to_delete(made_ons, today)


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route settlement anchors to `forecast_covariate`, then enforce retention.

    The prune runs in the same transaction (the CLI commits on success), so a failed load
    rolls back both. Imported lazily so importing the connector (for the registry) is DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_forecast_covariates

    written = upsert_forecast_covariates(session, df, run_id, code_to_id)
    prune(session)
    return written


def prune(session: Session) -> int:
    """Delete settlement vintages outside the retention window (returns rows deleted)."""
    return prune_vintages(session, source)


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Fetch every desired settlement vintage not already stored (+ a recent refresh overlap).

    `since` (framework contract) is ignored — the window is the retention keep-set,
    self-determined from what `forecast_covariate` already holds, so gaps are backfilled.
    """
    del since
    cfg = get_kpler_settings()
    today = dt.datetime.now(dt.UTC).date()

    desired = set(_desired_run_dates(today))
    have = loaded_run_dates(source)
    refresh = {today - dt.timedelta(days=i) for i in range(_REFRESH_DAYS)}
    trading_dates = sorted((desired - have) | (desired & refresh))

    cols = ["date", "long_name", "maturity_type", "value", "made_on"]
    if not trading_dates:
        return pd.DataFrame(columns=cols)
    log.info(
        "kpler_carbon_settles: %d trading-date(s), <=%d concurrent (today %s)",
        len(trading_dates),
        _CONCURRENCY,
        today,
    )

    rows = asyncio.run(_fetch_rows(cfg, trading_dates))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # `maturityDate` / `tradingDate` are plain "YYYY-MM-DD"; canonical columns are naive UTC.
        df["date"] = pd.to_datetime(df["date"])
        df["made_on"] = pd.to_datetime(df["made_on"])
    return df


async def _fetch_rows(
    cfg: KplerSettings, trading_dates: list[dt.date]
) -> list[tuple[Any, Any, Any, Any, str]]:
    """Fan out one request per trading date concurrently (bounded), collect raw settlement rows.

    Every emissions product (EUA / ETS2 / UKA) is returned as-is and discriminated in
    `to_canonical` (so the EUA filter is unit-testable). A `_CONCURRENCY` semaphore caps in-flight
    requests; `arequest` handles 429/5xx retries underneath. Non-trading days return an empty list.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(td: dt.date) -> list[tuple[Any, Any, Any, Any, str]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {"tradingDate": td.isoformat(), "provider": _PROVIDER},
                    label="kpler_carbon_settles",
                )
            return [
                (
                    d.get("maturityDate"),
                    d.get("longName"),
                    d.get("maturityType"),
                    d.get("settlementPrice"),
                    td.isoformat(),
                )
                for d in resp.json().get("data", [])
            ]

        chunks = await asyncio.gather(*(one(td) for td in trading_dates))
    return [row for chunk in chunks for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Keep the EUA monthly settlements, stamp the canonical series, carry `made_on`.

    Drops the other emissions products (`EEX EU ETS2 Future`, `EEX UKA Futures`), non-monthly
    contracts, and null settlements. No interpolation — anchors are stored raw (the `carbon_curve`
    transform splines them).
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
    if raw.empty:
        return pd.DataFrame(columns=out_cols)
    df = raw[(raw["long_name"] == _LONG_NAME) & (raw["maturity_type"] == _MATURITY_TYPE)]
    df = df[df["value"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    meta = series_dict()[0]
    df["series_id"] = meta["code"]
    df["name"] = meta["name"]
    df["group"] = meta["group"]
    df["sub_group"] = meta["sub_group"]
    df["area"] = meta["area"]
    df["value"] = df["value"].astype(float)
    df["source"] = source
    # The strip can list two EUA Future month rows for one (made_on, maturity), differing ~0.05
    # EUR; the curve needs one anchor each. Keep the higher, deterministically.
    return df.sort_values(["made_on", "date", "value"]).drop_duplicates(
        subset=["made_on", "date", "series_id"], keep="last"
    )[out_cols]
