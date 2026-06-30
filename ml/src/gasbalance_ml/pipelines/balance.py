"""Close the EU gas balance on forecasts and recompute the storage-level trajectory.

EU-wide single zone (plan decision — no NWE/CEE regional coupling). Per scenario:

    EU.DEMAND             = sum of demand-category component forecasts
    EU.SUPPLY             = sum of supply-category component forecasts
    EU.STORAGE.WITHDRAWAL = EU.DEMAND - EU.SUPPLY                 (the residual *plug*)
    EU.STORAGE.LEVEL[t]   = last_actual_level - cumsum(withdrawal[origin..t])

Storage withdrawal is the residual we back out of demand - supply (we don't forecast it
directly), so the forecast EU.STORAGE.WITHDRAWAL is *defined* by the closure — unlike the
actuals-side derived series (etl/settings/derived.yaml), which sums reported withdrawals.
EU.BALANCE is therefore identically 0 on forecasts (the plug closes it) and is not emitted.

Pure arithmetic — no DB, no model — so it's unit-testable on hand-built frames
(ml/tests/test_balance.py). The caller (cli.py) assembles the inputs via PostgresData.
Supply forecasting is a separate workstream; until it lands, supply components are absent
and withdrawal degenerates to demand.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

import pandas as pd

# Derived output series — already in the `series` table (etl derived.yaml, is_derived=true).
DEMAND = "EU.DEMAND"
SUPPLY = "EU.SUPPLY"
WITHDRAWAL = "EU.STORAGE.WITHDRAWAL"
LEVEL = "EU.STORAGE.LEVEL"

# code -> (category, sub_group, area)
Catalog = Mapping[str, tuple[str | None, str | None, str | None]]


def _side(category: str | None, sub_group: str | None) -> str | None:
    """Which balance side a component category feeds. Keep in sync with the EU.DEMAND /
    EU.SUPPLY selectors in etl/settings/derived.yaml so forecast and actuals close alike."""
    if category == "demand":
        return "demand"
    if category in ("production", "pipeline", "supply", "border_flows"):
        return "supply"
    if category == "lng" and sub_group is None:  # LNG sendout (not level/capacity)
        return "supply"
    return None


def _level_path(withdrawal: pd.Series, last_level: tuple[dt.date, float] | None) -> pd.Series:
    """level[t] = start - cumsum(withdrawal up to t). start = last actual level (else 0).
    Positive withdrawal draws the level down (legacy balance.py stage C).
    ponytail: no [0, capacity] clamp — legacy has none; add a band if levels go unphysical."""
    start = last_level[1] if last_level else 0.0
    return start - withdrawal.sort_index().cumsum()


def close_balance(
    components: pd.DataFrame,
    catalog: Catalog,
    last_level: tuple[dt.date, float] | None,
    *,
    made_on: dt.date,
    model_run_id: str = "",
) -> list[dict[str, object]]:
    """Close the balance for every scenario in `components` (cols: series_code, scenario,
    target_date, value). Returns forecast rows for EU.DEMAND/SUPPLY/STORAGE.WITHDRAWAL/LEVEL.
    """
    if components.empty:
        return []
    mrid = model_run_id or f"balance-{made_on}"

    def side_of(code: str) -> str | None:
        meta = catalog.get(code)
        return None if meta is None else _side(meta[0], meta[1])

    comp = components.copy()
    comp["_side"] = comp["series_code"].map(side_of)

    rows: list[dict[str, object]] = []
    for scenario, grp in comp.groupby("scenario", sort=True):
        demand = grp.loc[grp["_side"] == "demand"].groupby("target_date")["value"].sum()
        supply = grp.loc[grp["_side"] == "supply"].groupby("target_date")["value"].sum()
        dates = demand.index.union(supply.index)
        demand = demand.reindex(dates, fill_value=0.0)
        supply = supply.reindex(dates, fill_value=0.0)
        # ponytail: sign per the user's equation (withdrawal = demand - supply); one line to
        # flip if EU.BALANCE doesn't close once supply lands (derived.yaml:62-64 flags it).
        withdrawal = demand - supply
        level = _level_path(withdrawal, last_level)
        for code, series in (
            (DEMAND, demand),
            (SUPPLY, supply),
            (WITHDRAWAL, withdrawal),
            (LEVEL, level),
        ):
            for date, value in series.items():
                rows.append(
                    {
                        "series_code": code,
                        "target_date": pd.Timestamp(date).date(),
                        "scenario": str(scenario),
                        "model_run_id": mrid,
                        "made_on": made_on,
                        "value": float(value),
                    }
                )
    return rows
