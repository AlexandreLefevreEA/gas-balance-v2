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


if __name__ == "__main__":
    test_withdrawal_and_level_path()
    test_supply_absent_degrades_to_demand()
    print("ok")
