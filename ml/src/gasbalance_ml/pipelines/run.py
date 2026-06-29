"""Backtest + tune orchestration (development).

`run_backtest` evaluates a model over rolling origins and reports the error surface +
baseline skill. `run_tune` runs Optuna over the same walk-forward. Both pull the driver per
covariate_mode: `actual` = a fixed daily series (perfect foresight; the running layer feeds
a REF series here for a weather scenario), `vintage` = a per-origin point-in-time resolver.

ML stops at evaluation/inference — publishing forecasts (the `forecast` table, scenario
loops, the balance) is the running/balance layer. The data access is a small read-only
Protocol so this orchestration is unit-tested without a DB (ml/tests/test_pipeline.py).
MLflow logging is optional and lazily imported.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

from gasbalance_ml.evaluation.metrics import error_surface, mae, skill_score
from gasbalance_ml.evaluation.walkforward import walk_forward
from gasbalance_ml.models import get  # importing the package registers the built-in models


@dataclass
class Config:
    target_code: str
    horizon_days: int
    model: str = "lightgbm"
    model_params: dict[str, Any] = field(default_factory=dict)  # tune() output feeds inference
    mode: str = "actual"  # "actual" (perfect foresight) | "vintage" (audit)
    actual_temp_code: str | None = None  # covariate temp (actual mode)
    vintage_temp_code: str | None = None  # forecast_covariate temp (vintage mode)
    window: str = "expanding"
    sliding_years: int = 5
    track: bool = True  # log to MLflow


class DataSource(Protocol):
    def read_target(self, code: str) -> pd.Series: ...
    def read_daily_actual(self, code: str) -> pd.Series: ...
    def read_daily_vintage(self, code: str, origin: pd.Timestamp) -> pd.Series: ...


def _require(value: str | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required for this covariate_mode")
    return value


def _driver(data: DataSource, cfg: Config) -> Callable[[pd.Timestamp], pd.Series]:
    """The covariate_mode switch — a per-origin driver resolver."""
    if cfg.mode == "vintage":
        vcode = _require(cfg.vintage_temp_code, "vintage_temp_code")
        return lambda origin: data.read_daily_vintage(vcode, origin)
    series = data.read_daily_actual(_require(cfg.actual_temp_code, "actual_temp_code"))
    return lambda origin: series  # fixed across origins (perfect foresight)


def _overall_mae(res: pd.DataFrame) -> float:
    scored = res.dropna(subset=["y_true"])
    return float("nan") if scored.empty else mae(scored["y_true"], scored["y_pred"])


def _mlflow_log(cfg: Config, metrics: dict[str, float]) -> str:
    """Log one MLflow run; return its id ("" if tracking is off or mlflow is absent)."""
    if not cfg.track:
        return ""
    try:
        import mlflow
    except ImportError:
        return ""
    mlflow.set_experiment(f"gasbalance-{cfg.target_code}")
    with mlflow.start_run() as run:
        params: dict[str, Any] = {
            "model": cfg.model,
            "mode": cfg.mode,
            "horizon_days": cfg.horizon_days,
            "window": cfg.window,
        }
        params.update({f"hp_{k}": v for k, v in cfg.model_params.items()})
        mlflow.log_params(params)
        for key, val in metrics.items():
            if pd.notna(val):
                mlflow.log_metric(key, float(val))
        return str(run.info.run_id)


def run_backtest(data: DataSource, cfg: Config, origins: Sequence[pd.Timestamp]) -> dict[str, Any]:
    target = data.read_target(cfg.target_code)
    driver = _driver(data, cfg)
    model = get(cfg.model)
    res = walk_forward(
        target,
        driver,
        lambda: model(**cfg.model_params),
        origins,
        cfg.horizon_days,
        window=cfg.window,
        sliding_years=cfg.sliding_years,
    )
    base = walk_forward(
        target, driver, get("seasonal_naive"), origins, cfg.horizon_days, window=cfg.window
    )
    surface = error_surface(res)
    model_mae, base_mae = _overall_mae(res), _overall_mae(base)
    skill = skill_score(model_mae, base_mae)
    metrics: dict[str, float] = {"mae": model_mae, "baseline_mae": base_mae, "skill": skill}
    for r in surface.itertuples():
        metrics[f"mae_{r.bucket}"] = float(r.mae)
    model_run_id = _mlflow_log(cfg, metrics)
    return {
        "results": res,
        "surface": surface,
        "mae": model_mae,
        "baseline_mae": base_mae,
        "skill": skill,
        "model_run_id": model_run_id,
    }


def run_tune(
    data: DataSource, cfg: Config, origins: Sequence[pd.Timestamp], *, n_trials: int = 30
) -> dict[str, Any]:
    from gasbalance_ml.tuning.objective import tune

    target = data.read_target(cfg.target_code)
    driver = _driver(data, cfg)
    return tune(
        target,
        driver,
        origins,
        cfg.horizon_days,
        n_trials=n_trials,
        window=cfg.window,
        sliding_years=cfg.sliding_years,
    )
