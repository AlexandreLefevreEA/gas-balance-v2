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
Each side is summed with **skipna=False**: a component that is present but NaN on a date makes
that date's aggregate NaN (emitted as a gap, never a silently-low total). A side with *no*
components at all still degrades to 0 (e.g. before the supply workstream is wired).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping

import pandas as pd

log = logging.getLogger(__name__)

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


def _side_total(side_df: pd.DataFrame) -> pd.Series:
    """Sum one balance side's components per date with **skipna=False**: a component that is
    missing or NaN for a date makes that date's total NaN (surfaced downstream as a gap),
    never a silently-low sum that omits the missing series. Empty side -> empty (the caller
    degrades a side with *no* components at all to 0)."""
    if side_df.empty:
        return pd.Series(dtype=float)
    wide = side_df.pivot_table(
        index="target_date", columns="series_code", values="value", aggfunc="sum", dropna=False
    )
    return wide.sum(axis=1, skipna=False)


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
    skipped = 0
    for scenario, grp in comp.groupby("scenario", sort=True):
        demand_df = grp.loc[grp["_side"] == "demand"]
        supply_df = grp.loc[grp["_side"] == "supply"]
        demand = _side_total(demand_df)
        supply = _side_total(supply_df)
        dates = demand.index.union(supply.index)
        # A side with *no* components at all is "not forecast yet" -> 0 (degrade, legacy parity).
        # A side that HAS components but is incomplete on a date stays NaN -> emitted as a gap.
        demand = pd.Series(0.0, index=dates) if demand_df.empty else demand.reindex(dates)
        supply = pd.Series(0.0, index=dates) if supply_df.empty else supply.reindex(dates)
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
                if pd.isna(value):  # incomplete component -> NaN -> gap, not a silently-low cell
                    skipped += 1
                    continue
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
    if skipped:
        log.warning(
            "close_balance: %d EU cells incomplete (a component series is missing) -> left as gaps",
            skipped,
        )
    return rows
