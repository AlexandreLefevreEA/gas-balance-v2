"""Custom what-if scenarios — vectorised arithmetic overlays on forecast components.

A custom is a list of adjustment rules; each rule selects component series (by `code`, or by
`group`/`sub_group`/`area` like the derived.yaml selectors) and, over a `[from, to]` window,
applies one of:

    PERCENT       value *= v        (e.g. 1.10 = +10%)
    DELTA         value += v
    ABSOLUTE      value  = v
    PERIOD_TOTAL  spread v evenly over each matched series' in-window days

Ported from legacy params.xlsx `settings` (models/custom/models/*). Pure arithmetic — it never
refits a model — so a custom that touches only demand leaves supply byte-identical. That's the
"don't recompute what didn't change" guarantee: the expensive ML ran once for the base weather
forecasts; customs are pennies on top, and only the *touched* series get re-stored.

Forecast components are all future, so legacy's `override_actual` clamp (don't overwrite
realized history) can never trigger here — omitted on purpose.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

# code -> (category, sub_group, area)
Catalog = Mapping[str, tuple[str | None, str | None, str | None]]


def _matches(meta: tuple[str | None, str | None, str | None], sel: Mapping[str, Any]) -> bool:
    """A component (category, sub_group, area) matches a group/sub_group/area selector where
    each stated key agrees (mirrors etl compose._matches)."""
    cat, sub, area = meta
    return (
        ("group" not in sel or sel["group"] == cat)
        and ("sub_group" not in sel or sel["sub_group"] == sub)
        and ("area" not in sel or sel["area"] == area)
    )


def _selected_codes(catalog: Catalog, sel: Mapping[str, Any]) -> set[str]:
    """Codes a selector picks. `code` (str or list) short-circuits the metadata match."""
    if "code" in sel:
        code = sel["code"]
        return set(code) if isinstance(code, list) else {str(code)}
    return {c for c, meta in catalog.items() if _matches(meta, sel)}


def _apply(kind: str, values: pd.Series, value: float) -> pd.Series:
    if kind == "PERCENT":
        return values * value
    if kind == "DELTA":
        return values + value
    if kind == "ABSOLUTE":
        return pd.Series(value, index=values.index, dtype=float)
    raise ValueError(f"unknown adjustment type {kind!r}")


def apply_adjustments(
    components: pd.DataFrame,
    adjustments: list[dict[str, Any]],
    catalog: Catalog,
) -> tuple[pd.DataFrame, set[str]]:
    """Apply every rule to a single base scenario's components (cols include series_code,
    target_date, value). Returns (adjusted frame, set of touched series codes). Untouched
    rows are returned unchanged; only matched series inside each rule's window move."""
    out = components.copy()
    out["target_date"] = pd.to_datetime(out["target_date"])
    touched: set[str] = set()

    for adj in adjustments:
        codes = _selected_codes(catalog, adj.get("select", {}))
        if not codes:
            continue
        lo = pd.Timestamp(adj["from"]) if adj.get("from") else out["target_date"].min()
        hi = pd.Timestamp(adj["to"]) if adj.get("to") else out["target_date"].max()
        mask = out["series_code"].isin(codes) & out["target_date"].between(lo, hi)
        if not mask.any():
            continue
        touched |= set(out.loc[mask, "series_code"].unique())
        value = float(adj["value"])
        if adj["type"] == "PERIOD_TOTAL":
            for _, idx in out.loc[mask].groupby("series_code").groups.items():
                out.loc[idx, "value"] = value / len(idx)  # spread over that series' window days
        else:
            out.loc[mask, "value"] = _apply(adj["type"], out.loc[mask, "value"], value)

    out["target_date"] = out["target_date"].map(lambda t: pd.Timestamp(t).date())
    return out, touched
