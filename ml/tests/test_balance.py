"""close_balance: withdrawal = demand - supply, level = start - cumsum, supply-absent degrade."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from gasbalance_ml.pipelines.balance import close_balance

DATES = [dt.date(2026, 7, 1), dt.date(2026, 7, 2), dt.date(2026, 7, 3)]
CATALOG = {"D": ("demand", "LDZ", "DE"), "S": ("production", None, "NL")}
MADE = dt.date(2026, 6, 30)


def _components(demand: float, supply: float | None) -> pd.DataFrame:
    rows = [
        {"series_code": code, "scenario": "MEAN", "target_date": d, "value": val}
        for code, val in (("D", demand), ("S", supply))
        if val is not None
        for d in DATES
    ]
    return pd.DataFrame(rows)


def _by(rows: list[dict[str, object]], code: str) -> dict[object, float]:
    return {r["target_date"]: r["value"] for r in rows if r["series_code"] == code}  # type: ignore[misc]


def test_withdrawal_and_level_path() -> None:
    comps = _components(100.0, 30.0)
    rows = close_balance(comps, CATALOG, (MADE, 1000.0), made_on=MADE)

    assert _by(rows, "EU.DEMAND")[DATES[0]] == 100.0
    assert _by(rows, "EU.SUPPLY")[DATES[0]] == 30.0
    assert all(v == 70.0 for v in _by(rows, "EU.STORAGE.WITHDRAWAL").values())  # demand - supply
    level = _by(rows, "EU.STORAGE.LEVEL")
    assert level[DATES[0]] == 930.0  # 1000 - 70
    assert level[DATES[1]] == 860.0  # 1000 - 140
    assert level[DATES[2]] == 790.0  # 1000 - 210


def test_supply_absent_degrades_to_demand() -> None:
    rows = close_balance(_components(100.0, None), CATALOG, None, made_on=MADE)
    assert all(v == 0.0 for v in _by(rows, "EU.SUPPLY").values())
    assert all(v == 100.0 for v in _by(rows, "EU.STORAGE.WITHDRAWAL").values())
    assert _by(rows, "EU.STORAGE.LEVEL")[DATES[2]] == -300.0  # 0 - 300, no clamp (legacy parity)


def test_incomplete_component_becomes_a_gap_not_a_low_total() -> None:
    # Two supply series; the second is missing on DATES[1] -> that date's EU.SUPPLY must be a
    # GAP (no row), not 60 (which would silently omit the missing component).
    catalog = {
        "D": ("demand", "LDZ", "DE"),
        "S1": ("production", None, "NL"),
        "S2": ("lng", None, "GB"),
    }
    rows_in = [
        {"series_code": "D", "scenario": "MEAN", "target_date": d, "value": 100.0} for d in DATES
    ]
    for d in DATES:
        rows_in.append({"series_code": "S1", "scenario": "MEAN", "target_date": d, "value": 30.0})
    for d in (DATES[0], DATES[2]):  # S2 absent on DATES[1]
        rows_in.append({"series_code": "S2", "scenario": "MEAN", "target_date": d, "value": 30.0})

    rows = close_balance(pd.DataFrame(rows_in), catalog, None, made_on=MADE)
    supply = _by(rows, "EU.SUPPLY")
    assert supply[DATES[0]] == 60.0 and supply[DATES[2]] == 60.0
    assert DATES[1] not in supply  # gap, not 30
    # withdrawal/level past the gap are unknowable -> also gaps (level cumsums through NaN).
    assert DATES[1] not in _by(rows, "EU.STORAGE.WITHDRAWAL")


if __name__ == "__main__":
    test_withdrawal_and_level_path()
    test_supply_absent_degrades_to_demand()
    test_incomplete_component_becomes_a_gap_not_a_low_total()
    print("ok")
