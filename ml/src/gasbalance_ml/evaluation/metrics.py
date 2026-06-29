"""Pure forecast-error metrics + an error surface by horizon bucket."""

from __future__ import annotations

import numpy as np
import pandas as pd


def mae(y_true: pd.Series, y_pred: pd.Series) -> float:
    return float((y_true - y_pred).abs().mean())


def rmse(y_true: pd.Series, y_pred: pd.Series) -> float:
    return float(np.sqrt(((y_true - y_pred) ** 2).mean()))


def bias(y_true: pd.Series, y_pred: pd.Series) -> float:
    """Mean signed error (pred - true). +ve = over-forecast — compounds into storage."""
    return float((y_pred - y_true).mean())


def skill_score(model_mae: float, baseline_mae: float) -> float:
    """1 - model/baseline. >0 beats the baseline, 0 ties, <0 worse."""
    if baseline_mae == 0:
        return float("nan")
    return 1.0 - model_mae / baseline_mae


# (lo, hi) day-ahead horizon buckets, inclusive.
_BUCKETS: list[tuple[int, int, str]] = [
    (1, 1, "h1"),
    (2, 7, "h2-7"),
    (8, 30, "h8-30"),
    (31, 90, "h31-90"),
    (91, 365, "h91-365"),
    (366, 10**9, "h366+"),
]


def _bucket(horizon: int) -> str:
    for lo, hi, label in _BUCKETS:
        if lo <= horizon <= hi:
            return label
    return "other"


def error_surface(results: pd.DataFrame) -> pd.DataFrame:
    """results: columns [horizon, y_true, y_pred]. MAE/RMSE/Bias per horizon bucket."""
    cols = ["bucket", "n", "mae", "rmse", "bias"]
    df = results.dropna(subset=["y_true", "y_pred"]).copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["bucket"] = df["horizon"].map(_bucket)
    out: list[dict[str, object]] = []
    for _lo, _hi, label in _BUCKETS:
        g = df[df["bucket"] == label]
        if g.empty:
            continue
        out.append(
            {
                "bucket": label,
                "n": len(g),
                "mae": mae(g["y_true"], g["y_pred"]),
                "rmse": rmse(g["y_true"], g["y_pred"]),
                "bias": bias(g["y_true"], g["y_pred"]),
            }
        )
    return pd.DataFrame(out, columns=cols)
