"""The `select` step — assign the best model to each series (data-driven), persist the choice.

For each series it backtests the candidate models over rolling origins in perfect-foresight
mode (`mode=actual`, `KP.TEMP.<area>`) so the score reflects the weather->demand model, not
weather-forecast error — the same regime `tune` optimizes. The winner is the highest
walk-forward skill vs the `seasonal_naive` floor; if nothing beats the floor, the series keeps
`seasonal_naive`. The output is the series_models registry the forecast step reads, so the
operational forecast run never re-backtests. This is the periodic, offline step.

ponytail: only `lightgbm` is tunable today, so a candidate set of one is the common case —
"does tuned LightGBM beat the seasonal floor for this series?". The loop generalizes for free
as tunable models are added to `_TUNABLE`.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Any

import pandas as pd

from gasbalance_ml.pipelines.run import Config, DataSource, run_backtest, run_tune

log = logging.getLogger(__name__)

_TUNABLE = {"lightgbm"}  # models with an Optuna objective; others backtest with defaults


def select_series_model(
    data: DataSource,
    code: str,
    area: str,
    origins: Sequence[pd.Timestamp],
    *,
    horizon_days: int,
    candidates: Sequence[str],
    n_trials: int,
    track: bool,
    today: str,
) -> dict[str, Any]:
    """Backtest the candidates for one series; return the winning registry entry."""
    temp_code = f"KP.TEMP.{area}"
    # The floor: seasonal_naive (it IS run_backtest's baseline, so its own skill is ~0).
    best: dict[str, Any] = {
        "area": area,
        "model": "seasonal_naive",
        "params": {},
        "skill": 0.0,
        "model_run_id": f"seasonal_naive-{today}",
        "selected_on": today,
        "n_origins": len(origins),
    }
    for cand in candidates:
        cfg = Config(
            target_code=code,
            horizon_days=horizon_days,
            model=cand,
            mode="actual",
            actual_temp_code=temp_code,
            track=track,
        )
        if cand in _TUNABLE:
            cfg.model_params = run_tune(data, cfg, origins, n_trials=n_trials)
        out = run_backtest(data, cfg, origins)
        skill = out["skill"]
        log.info("select %s [%s]: skill=%.3f", code, cand, skill)
        if pd.notna(skill) and skill > best["skill"]:
            best = {
                "area": area,
                "model": cand,
                "params": cfg.model_params,
                "skill": float(skill),
                "model_run_id": out["model_run_id"] or f"{cand}-{today}",
                "selected_on": today,
                "n_origins": len(origins),
            }
    log.info("select %s -> %s (skill=%.3f)", code, best["model"], best["skill"])
    return best


def select_models(
    data: DataSource,
    universe: Sequence[tuple[str, str]],
    origins: Sequence[pd.Timestamp],
    *,
    horizon_days: int = 720,
    candidates: Sequence[str] = ("lightgbm",),
    n_trials: int = 30,
    track: bool = True,
    today: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Select the best model for every `(code, area)` in the universe. Returns the registry."""
    stamp = today or dt.date.today().isoformat()
    registry: dict[str, dict[str, Any]] = {}
    for code, area in universe:
        registry[code] = select_series_model(
            data,
            code,
            area,
            origins,
            horizon_days=horizon_days,
            candidates=candidates,
            n_trials=n_trials,
            track=track,
            today=stamp,
        )
    return registry
