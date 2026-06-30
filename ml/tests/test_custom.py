"""apply_adjustments: window-scoped, series-scoped arithmetic; untouched series stay identical."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from gasbalance_ml.pipelines.custom import apply_adjustments

DATES = [dt.date(2026, 7, d) for d in (1, 2, 3, 4, 5)]
CATALOG = {"D": ("demand", "LDZ", "DE"), "S": ("production", None, "NL")}


def _components() -> pd.DataFrame:
    rows = []
    for code, val in (("D", 100.0), ("S", 50.0)):
        rows += [
            {"series_code": code, "target_date": d, "scenario": "MEAN", "value": val} for d in DATES
        ]
    return pd.DataFrame(rows)


def _val(out: pd.DataFrame, code: str, day: dt.date) -> float:
    m = (out["series_code"] == code) & (out["target_date"] == day)
    return float(out.loc[m, "value"].iloc[0])


def test_percent_only_in_window_and_matched_series() -> None:
    adj = [
        {
            "select": {"group": "demand"},
            "type": "PERCENT",
            "value": 1.10,
            "from": "2026-07-02",
            "to": "2026-07-04",
        }
    ]
    out, touched = apply_adjustments(_components(), adj, CATALOG)

    assert touched == {"D"}  # the "don't recompute what didn't change" guarantee
    assert _val(out, "D", DATES[0]) == 100.0  # 07-01 out of window — unchanged
    assert abs(_val(out, "D", DATES[1]) - 110.0) < 1e-9  # 07-02 in window — +10%
    assert abs(_val(out, "D", DATES[3]) - 110.0) < 1e-9  # 07-04 in window (inclusive)
    assert _val(out, "D", DATES[4]) == 100.0  # 07-05 out of window
    assert all(_val(out, "S", d) == 50.0 for d in DATES)  # supply untouched, byte-identical


def _one(code: str, kind: str, value: float) -> list[dict[str, object]]:
    return [{"select": {"code": code}, "type": kind, "value": value}]


def test_delta_absolute_and_period_total() -> None:
    base = _components()
    out, _ = apply_adjustments(base, _one("S", "DELTA", -5.0), CATALOG)
    assert all(_val(out, "S", d) == 45.0 for d in DATES)

    out, _ = apply_adjustments(base, _one("D", "ABSOLUTE", 0.0), CATALOG)
    assert all(_val(out, "D", d) == 0.0 for d in DATES)

    # PERIOD_TOTAL spreads 1000 over D's 5 window days -> 200/day.
    out, _ = apply_adjustments(base, _one("D", "PERIOD_TOTAL", 1000.0), CATALOG)
    assert all(abs(_val(out, "D", d) - 200.0) < 1e-9 for d in DATES)


if __name__ == "__main__":
    test_percent_only_in_window_and_matched_series()
    test_delta_absolute_and_period_total()
    print("ok")
