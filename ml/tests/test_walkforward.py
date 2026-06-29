"""End-to-end walk-forward on synthetic data with a trivial model — no DB, no heavy deps."""

from __future__ import annotations

import numpy as np
import pandas as pd

from gasbalance_ml.evaluation.metrics import error_surface
from gasbalance_ml.evaluation.walkforward import predict_forward, walk_forward
from gasbalance_ml.models.base import Model
from gasbalance_ml.models.baseline import SeasonalNaive


class MeanModel(Model):
    """Predicts the training mean — enough to exercise the harness."""

    name = "mean"

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        del X
        self._m = float(y.mean())

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(self._m, index=X.index, dtype=float)


def test_walk_forward_runs_and_scores() -> None:
    idx = pd.date_range("2018-01-01", periods=1500, freq="D")
    temp = pd.Series(10 + 10 * np.sin(2 * np.pi * idx.dayofyear / 365.25), index=idx)
    target = 100 - 5 * (15.5 - temp).clip(lower=0)  # HDD-driven demand
    origins = [pd.Timestamp("2021-01-01"), pd.Timestamp("2021-07-01")]

    res = walk_forward(target, temp, MeanModel, origins, horizon_days=90)

    assert not res.empty
    assert {"origin", "target_date", "horizon", "y_pred", "y_true"}.issubset(res.columns)
    assert res["horizon"].min() == 0 and res["horizon"].max() == 89
    surf = error_surface(res)
    assert not surf.empty
    assert (surf["mae"] >= 0).all()


def test_predict_forward_returns_horizon_series() -> None:
    # The inference seam: any injected daily driver works — a REF-year series would plug in
    # identically (that's how the running layer gets a weather scenario, no ML change).
    idx = pd.date_range("2018-01-01", periods=1500, freq="D")
    temp = pd.Series(10 + 10 * np.sin(2 * np.pi * idx.dayofyear / 365.25), index=idx)
    target = 100 - 5 * (15.5 - temp).clip(lower=0)
    out = predict_forward(SeasonalNaive(), target, temp, pd.Timestamp("2021-06-01"), 90)
    assert len(out) == 90
    assert out.index.min() == pd.Timestamp("2021-06-01")
