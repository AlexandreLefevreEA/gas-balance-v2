"""Carbon (EUA) spline forward-curve transform — built from spot + futures settlements in the DB.

A transform under the standard ETL contract (`source / schema / fetch / to_canonical /
series_dict`), but `fetch` reads its inputs from Postgres instead of the network — the EUA **spot**
(`covariate` `KP.CARBON.SPOT`, loaded by `kpler_carbon_spot`) and the EUA monthly futures
**settlement** anchors (`forecast_covariate` `KP.CARBON.SETTLES`, loaded by `kpler_carbon_settles`).

Per trading date (`made_on`), it fits a **natural cubic spline** through
`[spot @ trading date] + [EUA settlement @ each contract maturity]` and samples it **daily** from
the trading date out to the last contract (~Dec-2034), writing the dense curve to
`forecast_covariate` as `KP.CARBON.CURVE` (one daily vintage per trading date).

Registered last (like `transforms/derived`) so `etl run all` runs it after the raw Kpler sources
have loaded (the CLI commits each connector before the next, ADR 0007). Storage is bounded by the
shared retention rule (re-runnable via `etl prune carbon_curve`); because the settles store is
already pruned to the keep-set, a full recompute each run stays bounded.

`gasbalance_core.db`/`.models` are imported inside `fetch`, not at module top: importing this
module (for the registry) must not create the engine, so the fixture-based tests stay DB-free.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import pandas as pd
from scipy.interpolate import CubicSpline

from gasbalance_etl.validation.forecast_covariate import forecast_covariate_carbon_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "carbon_curve"
schema = forecast_covariate_carbon_schema
is_transform = True  # reads raw outputs from Postgres -> the CLI runs it after the raw phase

_CODE = "KP.CARBON.CURVE"
_SPOT_CODE = "KP.CARBON.SPOT"  # covariate, loaded by kpler_carbon_spot
_SETTLES_CODE = "KP.CARBON.SETTLES"  # forecast_covariate anchors, loaded by kpler_carbon_settles

_OUT_COLS = [
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


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = the single derived EU carbon forward-curve series (hardcoded)."""
    return [
        {
            "code": _CODE,
            "name": "EU carbon (EUA) spline forward curve",
            "group": "carbon",
            "sub_group": "eua_curve",
            "area": "EU",
            "unit": "EUR/tCO2",
            "is_derived": True,
        }
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route the daily curve to `forecast_covariate`, then enforce retention."""
    from gasbalance_etl.load.upsert import upsert_forecast_covariates

    written = upsert_forecast_covariates(session, df, run_id, code_to_id)
    prune(session)
    return written


def prune(session: Session) -> int:
    """Delete curve vintages outside the retention window (shared rule). Returns rows deleted.

    `prune_vintages` is imported lazily: importing it pulls in the `connectors` package, which
    imports this transform to build the registry — a top-level import would be circular.
    """
    from gasbalance_etl.connectors._kpler_common import prune_vintages

    return prune_vintages(session, source)


def _spline_curve(
    made_on: dt.date, anchors: list[tuple[dt.date, float]]
) -> list[tuple[dt.date, float]]:
    """Natural cubic spline through the anchors, sampled daily from `made_on` to the last node.

    `anchors` is `(date, value)` with the spot ordered **first** (so it wins a maturity==made_on
    tie). Nodes are keyed by `x = (date - made_on).days`; anchors before the trading date (x < 0)
    are dropped so the spot stays the near anchor. Returns `[]` if fewer than 2 nodes remain.

    # ponytail: bc_type="natural" — calm ends, no overshoot; switch to "not-a-knot" if the long
    # annual-only tail ever needs a livelier fit.
    """
    node: dict[int, float] = {}
    for d, v in anchors:
        x = (d - made_on).days
        if x < 0:
            continue
        node.setdefault(x, float(v))  # spot passed first -> wins the x==0 tie
    if len(node) < 2:
        return []
    xs = sorted(node)
    ys = [node[x] for x in xs]
    cs = CubicSpline(xs, ys, bc_type="natural")
    return [(made_on + dt.timedelta(days=day), float(cs(day))) for day in range(xs[-1] + 1)]


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Read the spot + settlement anchors from Postgres into a long anchors frame.

    `since` (framework contract) is ignored — full recompute; the idempotent upsert makes re-runs
    safe. Columns: `[made_on, anchor_date, value]`; per trading date one spot row
    (`anchor_date == made_on`) plus one row per EUA contract maturity. Trading dates with no stored
    spot are skipped (the curve must start at the spot).
    """
    del since  # full recompute
    from sqlalchemy import select

    from gasbalance_core.db import SessionLocal
    from gasbalance_core.models import Covariate, ForecastCovariate, Series

    cols = ["made_on", "anchor_date", "value"]
    with SessionLocal() as session:
        spot_rows = [
            tuple(r)
            for r in session.execute(
                select(Covariate.ts, Covariate.value)
                .join(Series, Covariate.series_id == Series.id)
                .where(Series.code == _SPOT_CODE)
            ).all()
        ]
        settle_rows = [
            tuple(r)
            for r in session.execute(
                select(ForecastCovariate.made_on, ForecastCovariate.ts, ForecastCovariate.value)
                .join(Series, ForecastCovariate.series_id == Series.id)
                .where(Series.code == _SETTLES_CODE)
            ).all()
        ]

    spot_by_date: dict[dt.date, float] = {ts.date(): float(v) for ts, v in spot_rows}
    settles_by_made_on: dict[dt.date, list[tuple[dt.datetime, float]]] = defaultdict(list)
    for made_on, ts, value in settle_rows:
        settles_by_made_on[made_on].append((ts, float(value)))

    records: list[dict[str, Any]] = []
    for made_on, items in settles_by_made_on.items():
        spot_v = spot_by_date.get(made_on)
        if spot_v is None:
            log.warning("carbon_curve: no spot for trading date %s; skipping vintage", made_on)
            continue
        mo = pd.Timestamp(made_on)
        records.append({"made_on": mo, "anchor_date": mo, "value": spot_v})  # spot = near anchor
        for ts, value in items:
            records.append({"made_on": mo, "anchor_date": pd.Timestamp(ts.date()), "value": value})

    if not records:
        log.info("carbon_curve: no (spot + settles) vintages available yet")
        return pd.DataFrame(columns=cols)
    return pd.DataFrame.from_records(records)[cols]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Spline each trading date's anchors into a daily curve, stamped canonical + `made_on`.

    Pure (no DB / network): groups the anchors frame by `made_on`, splines via `_spline_curve`
    (spot ordered first), and emits one canonical row per sampled day.
    """
    if raw.empty:
        return pd.DataFrame(columns=_OUT_COLS)

    records: list[dict[str, Any]] = []
    for made_on_ts, g in raw.groupby("made_on"):
        made_on = pd.Timestamp(made_on_ts).date()
        spot = g[g["anchor_date"] == made_on_ts]  # near anchor(s), ordered first
        fut = g[g["anchor_date"] != made_on_ts]
        anchors: list[tuple[dt.date, float]] = [
            (pd.Timestamp(d).date(), float(v))
            for d, v in zip(spot["anchor_date"], spot["value"], strict=True)
        ]
        anchors += [
            (pd.Timestamp(d).date(), float(v))
            for d, v in zip(fut["anchor_date"], fut["value"], strict=True)
        ]
        for d, value in _spline_curve(made_on, anchors):
            records.append(
                {"date": pd.Timestamp(d), "value": value, "made_on": pd.Timestamp(made_on)}
            )

    if not records:
        return pd.DataFrame(columns=_OUT_COLS)

    meta = series_dict()[0]
    df = pd.DataFrame.from_records(records)
    df["series_id"] = meta["code"]
    df["name"] = meta["name"]
    df["group"] = meta["group"]
    df["sub_group"] = meta["sub_group"]
    df["area"] = meta["area"]
    df["source"] = source
    return df[_OUT_COLS]
