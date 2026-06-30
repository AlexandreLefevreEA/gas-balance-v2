"""LightGBM regressor behind the Model interface — the LDZ workhorse.

`lightgbm` is imported lazily inside `fit()` so importing the registry stays
dependency-free (the pure tests need only pandas/numpy). Hyperparameters are tuned by
the Optuna objective; the defaults here are a sane starting point.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from gasbalance_ml.models.base import Model, register

_DEFAULTS: dict[str, Any] = {
    "n_estimators": 400,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 0,
    "n_jobs": -1,
    "verbosity": -1,
}


@register
class LightGBMModel(Model):
    name = "lightgbm"

    def __init__(self, **params: Any) -> None:
        self.params: dict[str, Any] = {**_DEFAULTS, **params}
        self._model: Any = None

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        import lightgbm as lgb

        self._model = lgb.LGBMRegressor(**self.params)
        # Keep the DataFrame (named features) on both fit and predict — passing a bare ndarray
        # on one side and not the other is what triggers sklearn's feature-name mismatch warning.
        self._model.fit(X, y)

    def predict(self, X: pd.DataFrame) -> pd.Series:
        if self._model is None:
            raise RuntimeError("LightGBMModel.predict called before fit")
        preds = self._model.predict(X)
        return pd.Series(preds, index=X.index, dtype=float)
