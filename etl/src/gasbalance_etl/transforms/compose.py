"""Shared series-composition primitive (see etl/CLAUDE.md → transforms).

`compose(entries, wide, source)` turns a wide frame into canonical rows: each
entry's value = sum(positive columns) - sum(negative columns), aligned by date
(skipna=False). The CE connector uses it with raw CE seriesIds as columns; the
derived stage uses it with v2 series codes. `referenced_ids` lists the input
columns an entry set needs, so a fetch knows what to pull.

The derived stage selects its inputs by `group`/`sub_group`/`area` rather than by
listing codes (legacy aggregates by category). `resolve(entries, catalog)` expands
those `positive_select`/`negative_select` filters into concrete code lists, so the
same `compose`/`referenced_ids` work unchanged. These two helpers are pure (no DB)
to stay unit-testable.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

_CANONICAL_COLS = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]


def referenced_ids(entries: list[dict[str, Any]]) -> list[str]:
    """Unique input ids referenced by the entries (order-preserving)."""
    seen: dict[str, None] = {}
    for e in entries:
        for cid in (e.get("positive") or []) + (e.get("negative") or []):
            seen[cid] = None
    return list(seen)


def _matches(meta: dict[str, Any], sel: dict[str, Any]) -> bool:
    """A series (its catalog metadata) matches a selector if every stated key agrees.

    `group` is required; `sub_group`/`area` are matched only when the selector states
    them (an explicit `null` matches a NULL value — e.g. LNG sendout vs level/capacity).
    """
    return (
        sel.get("group") == meta.get("group")
        and ("sub_group" not in sel or sel["sub_group"] == meta.get("sub_group"))
        and ("area" not in sel or sel["area"] == meta.get("area"))
    )


def select_codes(catalog: dict[str, dict[str, Any]], selectors: list[dict[str, Any]]) -> list[str]:
    """Codes in `catalog` ({code: {group, sub_group, area}}) matching any selector."""
    if not selectors:
        return []
    return sorted(c for c, meta in catalog.items() if any(_matches(meta, s) for s in selectors))


def resolve(
    entries: list[dict[str, Any]], catalog: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Expand each entry's `*_select` group filters into explicit positive/negative codes."""
    out = []
    for e in entries:
        pos = list(e.get("positive") or []) + select_codes(catalog, e.get("positive_select") or [])
        neg = list(e.get("negative") or []) + select_codes(catalog, e.get("negative_select") or [])
        out.append({**e, "positive": pos, "negative": neg})
    return out


def compose(entries: list[dict[str, Any]], wide: pd.DataFrame, source: str) -> pd.DataFrame:
    """Compose each entry from input columns: sum(positive) - sum(negative)."""
    have = set(wide.columns)
    frames = []
    for e in entries:
        pos = e.get("positive") or []
        neg = e.get("negative") or []
        missing = [c for c in pos + neg if c not in have]
        if missing:
            log.warning("%s: %s missing input ids %s; skipped", source, e["code"], missing)
            continue
        val = wide[pos].sum(axis=1, skipna=False) if pos else pd.Series(0.0, index=wide.index)
        if neg:
            val = val.sub(wide[neg].sum(axis=1, skipna=False))

        if e.get("fillna") == "0":
            val = val.fillna(0)

        val = val.dropna()
        if e.get("skip_last_day") and len(val):
            val = val.iloc[:-1]  # legacy: last day often incomplete for these series
        if val.empty:
            continue
        frames.append(
            pd.DataFrame(
                {
                    "date": val.index,
                    "series_id": e["code"],
                    "name": e["name"],
                    "group": e.get("group"),
                    "sub_group": e.get("sub_group"),
                    "area": e.get("area"),
                    "value": val.to_numpy(),
                    "source": source,
                }
            )
        )

    if not frames:
        return pd.DataFrame(columns=_CANONICAL_COLS)
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df[_CANONICAL_COLS]
