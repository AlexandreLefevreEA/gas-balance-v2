"""Energy Quantified coal forward-curve connector — daily coal forward prices, a forecast covariate.

Source: `GET /ohlc/{curve}/latest/?date={tradingDate}` for the ICE **Coal API-2 (CIF ARA)** futures
curve (`Futures Coal API-2 USD/t ICE OHLC`, USD/t). The response is the OHLC ladder of listed
futures for one trading date — monthly, quarterly, seasonal and yearly contracts. We keep the
**monthly** strip (~48 contracts, ~4 years out) and **cubic-spline its settlements onto a daily
grid** — the daily coal forward curve, an exogenous covariate (coal-vs-gas switching drives
gas-for-power demand).

**Why interpolate (unlike the Kpler forward curves).** Kpler returns its gas/power forward curves
already at daily grain; EQ returns discrete contract settlements, so the daily curve is built here
via a natural cubic spline through the monthly settles (one knot per contract at its delivery-month
midpoint), evaluated at every day strictly inside the knot range (no extrapolation).

**The vintage dimension.** A forward curve is `(tradingDate, deliveryDate) -> price`: the same
delivery day is re-priced on every trading date. Values land in `forecast_covariate`, keyed by
`(series_id, made_on, ts)` (`made_on` = the trading date), not the single-vintage `covariate`.
See ADR 0009.

**`made_on` comes from the response, not the request.** `GET .../latest/?date=D` returns the latest
curve as of `D`: a weekend/holiday `date` returns the prior trading day's curve, with
`product.traded_at` set to that prior day. We key on `traded_at`, so a non-trading `date` just
re-confirms an already-stored vintage (idempotent) instead of duplicating it under a fake date.

**Retention.** Same rule as the other `*_forecast` connectors (and re-runnable via
`etl prune eq_coal_curve`): keep **all** trading dates from the last 15 days, plus **every Monday**
for 1 year; delete the rest.

**Refresh: self-managing, and backfills missing vintages.** Each run computes the desired keep-set
of trading dates, subtracts what's already stored, and fetches every missing vintage plus a small
recent overlap re-pulled for late revisions. The keep-set is filtered to weekdays — no settlement
on weekends (a weekend request returns the prior Friday's curve, harmlessly re-confirmed).

**Request grain.** One curve per request, so **one GET per trading date** — fanned out concurrently
(bounded by `_CONCURRENCY`) over the shared 429/5xx retry. Auth: `X-API-Key` header (`EQ_API_KEY`).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
import pandas as pd
from scipy.interpolate import CubicSpline

from gasbalance_etl.connectors._kpler_common import (
    desired_run_dates,
    loaded_run_dates,
    prune_vintages,
    vintages_to_delete,
)
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.eq_coal_curve.config import get_eq_settings
from gasbalance_etl.validation.forecast_covariate import forecast_covariate_coal_price_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.eq_coal_curve.config import EqSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "eq_coal_curve"
schema = forecast_covariate_coal_price_schema

# ponytail: one curve (ICE Coal API-2) -> hardcode the single series. Add a settings YAML mapping
# {code, curve, area, unit} only when a second EQ coal curve appears.
_CURVE = "Futures Coal API-2 USD/t ICE OHLC"
_CODE = "EQ.COALFC.API2"
_NAME = "Coal API-2 (CIF ARA) forward curve"
_AREA = "ARA"  # API-2 = CIF ARA (Amsterdam-Rotterdam-Antwerp) coal
_UNIT = "USD/t"
_GROUP = "coal_forward_curve"

# Re-pull the most recent few trading dates each run, to catch a late settlement revision.
_REFRESH_DAYS = 3
# ponytail: bound the concurrent in-flight requests. One GET per trading date, so a first backfill
# is ~63 GETs (the weekday keep-set: ~11 recent weekdays + ~52 Mondays) and a handful incrementally
# — fan them out instead of looping serially, capped so we don't trip the rate limit (the shared
# helper still retries 429/5xx). Raise if the API tolerates more.
_CONCURRENCY = 8
# ponytail: no _HISTORY_START floor. API-2 history runs years back, covering the whole trailing-year
# keep-set, so every weekday vintage we request returns a curve.


def series_dict() -> list[dict[str, Any]]:
    """The single curated series for this source (one curve)."""
    return [
        {
            "code": _CODE,
            "name": _NAME,
            "group": _GROUP,
            "sub_group": _UNIT,
            "area": _AREA,
            "unit": _UNIT,
        }
    ]


def _desired_run_dates(today: dt.date) -> list[dt.date]:
    """Fetch keep-set = the shared retention rule, minus weekends (no settlement on weekends)."""
    return [d for d in desired_run_dates(today) if d.weekday() < 5]


def _vintages_to_delete(made_ons: list[dt.date], today: dt.date) -> set[dt.date]:
    """This connector's retention rule (shared, pure); `prune` wraps it in SQL."""
    return vintages_to_delete(made_ons, today)


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route curve rows to `forecast_covariate`, then enforce retention.

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
    """Fetch every desired curve vintage not already stored (+ a recent refresh overlap).

    `since` (framework contract) is ignored — the window is the retention keep-set,
    self-determined from what `forecast_covariate` already holds, so gaps are backfilled.
    """
    del since
    cfg = get_eq_settings()
    today = dt.datetime.now(dt.UTC).date()

    desired = set(_desired_run_dates(today))
    have = loaded_run_dates(source)
    refresh = {today - dt.timedelta(days=i) for i in range(_REFRESH_DAYS)}
    # clamp refresh to the keep-set: never re-fetch a vintage that just aged out of the window
    # (only for prune to delete it next run), nor a weekend (not in `desired`).
    run_dates = sorted((desired - have) | (desired & refresh))

    cols = ["made_on", "period", "delivery", "value"]
    if not run_dates:
        return pd.DataFrame(columns=cols)
    log.info(
        "eq-coal: %d trading-dates, <=%d concurrent (today %s)", len(run_dates), _CONCURRENCY, today
    )

    rows = asyncio.run(_fetch_rows(cfg, run_dates))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["delivery"] = pd.to_datetime(df["delivery"])  # 'YYYY-MM-DD' delivery-period start
        df["made_on"] = pd.to_datetime(df["made_on"])  # 'YYYY-MM-DD' trading date (from traded_at)
    return df


async def _fetch_rows(
    cfg: EqSettings, run_dates: list[dt.date]
) -> list[tuple[str, str, str, float]]:
    """Fan out one request per trading date concurrently (bounded), collect raw contract rows.

    Returns `(traded_at, period, delivery, settlement)` per contract that has a settlement;
    `traded_at` (not the requested date) is the curve's real trading date. `arequest` handles
    429/5xx retries underneath. The curve name is path-encoded (it has spaces and a `/`).
    """
    endpoint = f"ohlc/{quote(_CURVE, safe='')}/latest/"
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"X-API-Key": cfg.api_key, "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(rd: dt.date) -> list[tuple[str, str, str, float]]:
            async with sem:
                resp = await arequest(client, endpoint, {"date": rd.isoformat()}, label="eq-coal")
            return [
                (p["traded_at"], p["period"], p["delivery"], d["settlement"])
                for d in resp.json().get("data", [])
                if (p := d.get("product")) and d.get("settlement") is not None
            ]

        chunks = await asyncio.gather(*(one(rd) for rd in run_dates))
    return [row for chunk in chunks for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Spline each vintage's monthly settles to a daily curve, stamped as the single series.

    Per `made_on`: keep the monthly contracts, fit a natural cubic spline through `(delivery-month
    midpoint, settlement)`, and evaluate at every day strictly inside the knot range (no
    extrapolation). Other period types (quarter/season/year) are ignored.
    """
    cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source", "made_on"]
    if raw.empty:
        return pd.DataFrame(columns=cols)

    monthly = raw[raw["period"] == "month"]
    daily: list[pd.DataFrame] = []
    for made_on, g in monthly.groupby("made_on"):
        g = g.drop_duplicates("delivery")
        if len(g) < 2:  # need >=2 knots to interpolate
            continue
        daily.append(_spline_daily(g).assign(made_on=made_on))
    if not daily:
        return pd.DataFrame(columns=cols)

    df = pd.concat(daily, ignore_index=True)
    meta = series_dict()[0]
    df["series_id"] = meta["code"]
    df["name"] = meta["name"]
    df["group"] = meta["group"]
    df["sub_group"] = meta["sub_group"]
    df["area"] = meta["area"]
    df["source"] = source
    return df[cols]


def _spline_daily(g: pd.DataFrame) -> pd.DataFrame:
    """Natural cubic spline through one vintage's monthly settles -> a daily (date, value) frame.

    Knot x = the delivery month's midpoint (a monthly future settles ~the month's average, so its
    representative point is mid-month); the daily grid spans only the interior of the knot range, so
    no value is extrapolated. ponytail: midpoint + interior-only grid is the chosen convention;
    switch the anchor to the delivery start, or extrapolate to the period bounds, if the near-term
    fortnight is ever needed.
    """
    g = g.sort_values("delivery")
    starts = g["delivery"]
    mids = starts + (starts + pd.offsets.MonthBegin(1) - starts) / 2  # mid-delivery-month
    origin = mids.min()  # rebase to small floats so the spline solve stays well-conditioned
    x = ((mids - origin) / pd.Timedelta(days=1)).to_numpy()
    y = g["value"].to_numpy(dtype=float)
    cs = CubicSpline(x, y, bc_type="natural")
    days = pd.date_range(mids.min().ceil("D"), mids.max().floor("D"), freq="D")
    xi = ((days - origin) / pd.Timedelta(days=1)).to_numpy()
    return pd.DataFrame({"date": days, "value": cs(xi)})
