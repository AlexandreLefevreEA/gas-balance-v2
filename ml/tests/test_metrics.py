"""Pure metric checks + the horizon error surface."""

from __future__ import annotations

import pandas as pd

from gasbalance_ml.evaluation.metrics import bias, error_surface, mae, rmse, skill_score


def test_basic_metrics() -> None:
    t = pd.Series([1.0, 2.0, 3.0])
    p = pd.Series([1.0, 2.0, 4.0])
    assert mae(t, p) == 1 / 3
    assert rmse(t, p) == (1 / 3) ** 0.5
    assert bias(t, p) == 1 / 3  # over-forecast
    assert skill_score(0.5, 1.0) == 0.5  # half the baseline error


def test_error_surface_buckets() -> None:
    results = pd.DataFrame(
        {
            "horizon": [1, 5, 5, 40, 400],
            "y_true": [10.0, 10.0, 10.0, 10.0, 10.0],
            "y_pred": [11.0, 12.0, 8.0, 10.0, 5.0],
        }
    )
    surf = error_surface(results).set_index("bucket")
    assert set(surf.index) == {"h1", "h2-7", "h31-90", "h366+"}
    assert surf.loc["h1", "mae"] == 1.0
    assert surf.loc["h2-7", "n"] == 2
    assert surf.loc["h366+", "bias"] == -5.0  # under-forecast at long horizon


def test_error_surface_empty() -> None:
    empty = pd.DataFrame({"horizon": [1], "y_true": [float("nan")], "y_pred": [1.0]})
    assert error_surface(empty).empty
