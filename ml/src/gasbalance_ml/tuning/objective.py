"""Optuna hyperparameter search for the LightGBM LDZ model.

The objective is the **mean walk-forward MAE across folds** (the plan's fix for legacy's
single 365-day split), evaluated in the fast perfect-foresight regime — so tuning
optimizes the structural weather->demand model, not weather-forecast noise. `optuna` is
imported lazily so the package imports without it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pandas as pd

from gasbalance_ml.evaluation.metrics import mae
from gasbalance_ml.evaluation.walkforward import walk_forward
from gasbalance_ml.models.lgbm import LightGBMModel

type Driver = pd.Series | Callable[[pd.Timestamp], pd.Series]


def tune(
    target: pd.Series,
    driver: Driver,
    origins: Sequence[pd.Timestamp],
    horizon_days: int,
    *,
    n_trials: int = 30,
    window: str = "expanding",
    sliding_years: int = 5,
) -> dict[str, Any]:
    import optuna

    def objective(trial: Any) -> float:
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 100, 800),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 255),
            "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        }
        res = walk_forward(
            target,
            driver,
            lambda: LightGBMModel(**params),
            origins,
            horizon_days,
            window=window,
            sliding_years=sliding_years,
        )
        scored = res.dropna(subset=["y_true"])
        return float("inf") if scored.empty else mae(scored["y_true"], scored["y_pred"])

    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=n_trials)
    return dict(study.best_params)
