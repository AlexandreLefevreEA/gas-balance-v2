"""Kpler power price-forward-curve connector — forward power prices as a forecast covariate.

Source: `GET /power/prices/price-forward-curve/power`, the power-price **forward curve** per
zone (EUR/MWh), built from the EEX futures settlements of a given **trading date**. Used as an
exogenous **covariate** for gas-for-power demand (forward power prices drive gas dispatch).

One series per zone, code `KP.PFC.<zone>`; `sub_group` holds the `demandPeriod` (`base`). We pull
the `main` scenario and `base` (baseload) demand period — the standard reference forward price.
(`peak`/`off_peak` demand periods and the weather/sensitivity scenarios are one-line extensions:
loop `_DEMAND_PERIOD` / `_SCENARIO` and tag `sub_group`/code.)

**The vintage dimension.** A forward curve is `(tradingDate, deliveryDate) → value`: each trading
day re-settles the whole curve, so the same delivery day appears in every trading date's curve.
Values land in `forecast_covariate`, keyed by `(series_id, made_on, ts)` (`made_on` = the
**trading date**), not the single-vintage `covariate`. See ADR 0009.

**Retention.** Multi-vintage storage is bounded by the shared rule enforced after every load (and
re-runnable via `etl prune kpler_power_forward_curve`): keep **all** trading dates from the last
15 days, plus **every Monday** for 1 year; delete the rest. Same rule as the other `*_forecast`
connectors.

**Refresh: self-managing, and backfills missing vintages.** Each run computes the desired keep-set
of trading dates, subtracts what's already stored, and fetches every missing vintage plus a small
recent overlap re-pulled for revisions.

**Request grain.** `zones` batches **all zones** into one request (plain `zones` array), so we
issue **one request per trading date** — fanned out concurrently (bounded by `_CONCURRENCY`) over
the shared 429/5xx retry. Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler
connectors).

**Endpoint quirks** (probed live, differ from the loads/generations forecasts):
- **No `timezone` param** — sending it returns HTTP 422.
- **Germany is `DE-LU`** (the bidding-zone enum); plain `DE` → 422 (as in generation_forecast).
- **`tradingDate` is required**; `scenarios`/`demandPeriod`/`granularity` default to
  `main`/`base`/`hourly` — we pass them explicitly and pick `daily`.
- Weekend / holiday trading dates return 0 rows (no EEX settlement) and are harmlessly
  re-requested each run (a few empty GETs) — see the `_HISTORY_START` note below.
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
from gasbalance_etl.validation.forecast_covariate import forecast_covariate_power_price_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_power_forward_curve"
schema = forecast_covariate_power_price_schema

_ENDPOINT = "power/prices/price-forward-curve/power"
# The curve runs years out (to ~2030) at any granularity; `daily` (~1.65k pts/zone/vintage) is
# the chosen resolution — fine-grained enough to shape the gas-for-power signal without the
# ~40k-pt/vintage hourly blow-up. `scenario=main` + `demandPeriod=base` = the reference baseload
# forward (the API defaults, passed explicitly so a default change can't silently shift the data).
_GRANULARITY = "daily"
_SCENARIO = "main"
_DEMAND_PERIOD = "base"
# Re-pull the most recent few trading dates each run, to catch revised settlements.
_REFRESH_DAYS = 3
# ponytail: bound the concurrent in-flight requests. `zones` batches into one request per trading
# date, so a run is at most ~67 GETs on the first backfill (15 daily + 52 Mondays) and a handful
# incrementally — fan them out instead of looping serially, capped so we don't trip the rate limit
# (the shared helper still retries 429/5xx). Raise if the API tolerates more.
_CONCURRENCY = 8
# ponytail: no _HISTORY_START floor (unlike kpler_generation_forecast). The curve's history starts
# ~2023, covering the whole trailing-year keep-set, so the Monday vintages we request return data.
# Weekend/holiday trading dates inside the 15-day window return 0 rows and get re-requested each
# run (a few empty GETs) — cheap; not worth a trading-calendar to special-case.


def _code(zone: str) -> str:
    """Series code, e.g. KP.PFC.FR / KP.PFC.DE-LU."""
    return f"KP.PFC.{zone}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML -> one baseload forward-curve series per zone."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"]),
            "name": f"{z['zone']} power price forward curve",
            "group": "price_forward_curve",
            "sub_group": _DEMAND_PERIOD,
            "area": z["area"],
            "unit": "EUR/MWh",
            "zone": z["zone"],  # used by to_canonical merge + fetch
        }
        for z in zones
    ]


def _desired_run_dates(today: dt.date) -> list[dt.date]:
    """This connector's fetch keep-set = the shared retention rule (no history floor)."""
    return desired_run_dates(today)


def _vintages_to_delete(made_ons: list[dt.date], today: dt.date) -> set[dt.date]:
    """This connector's retention rule (shared, pure); `prune` wraps it in SQL."""
    return vintages_to_delete(made_ons, today)


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route forward-curve rows to `forecast_covariate`, then enforce retention.

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
    """Fetch every desired forward-curve vintage not already stored (+ a recent refresh overlap).

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
    # clamp refresh to the keep-set: never re-fetch a vintage that just aged out of the window
    # (only for prune to delete it next run)
    trading_dates = sorted((desired - have) | (desired & refresh))

    cols = ["zone", "date", "value", "made_on"]
    if not trading_dates:
        return pd.DataFrame(columns=cols)
    log.info(
        "kpler-pfc: %d trading-dates x %d zones, <=%d concurrent (today %s)",
        len(trading_dates),
        len(zones),
        _CONCURRENCY,
        today,
    )

    rows = asyncio.run(_fetch_rows(cfg, trading_dates, zones))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # Kpler returns tz-aware UTC ISO timestamps; canonical `date` is naive UTC.
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
        df["made_on"] = pd.to_datetime(df["made_on"])  # 'YYYY-MM-DD' -> naive datetime64
    return df


async def _fetch_rows(
    cfg: KplerSettings, trading_dates: list[dt.date], zones: list[str]
) -> list[tuple[str, str, float, str]]:
    """Fan out one request per trading date concurrently (bounded), collect raw rows.

    `zones` batches into a single request per trading date (all zones at once); the response
    echoes `zone` and `tradingDate`. A `_CONCURRENCY` semaphore caps in-flight requests;
    `arequest` handles 429/5xx retries underneath. Note: no `timezone` param (the endpoint
    rejects it with 422).
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(td: dt.date) -> list[tuple[str, str, float, str]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "zones": zones,
                        "tradingDate": td.isoformat(),
                        "scenarios": _SCENARIO,
                        "demandPeriod": _DEMAND_PERIOD,
                        "granularity": _GRANULARITY,
                    },
                    label="kpler-pfc",
                )
            return [
                (d["zone"], d["startDate"], d["value"], d["tradingDate"])
                for d in resp.json().get("data", [])
                if d.get("value") is not None
            ]

        chunks = await asyncio.gather(*(one(td) for td in trading_dates))
    return [row for chunk in chunks for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map each zone's daily forward-curve rows to canonical series, carrying `made_on`."""
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
    df = raw.merge(meta, on=["zone"], how="inner")  # unknown zones drop out
    df["source"] = source
    cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source", "made_on"]
    return df[cols]
