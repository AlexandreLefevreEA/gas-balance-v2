"""select_series_model: pick the highest-skill candidate, else fall back to the floor."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from gasbalance_ml.pipelines import select as sel

ORIGINS = [pd.Timestamp("2021-01-01"), pd.Timestamp("2021-03-01")]
# run_tune/run_backtest are monkeypatched, so select_series_model never touches the data
# source — a bare stand-in is enough (typed Any to satisfy the DataSource param).
_DATA: Any = object()


def _fake_tune(data: Any, cfg: Any, origins: Any, n_trials: int) -> dict[str, Any]:
    return {"n_estimators": 123}


def _fake_backtest(data: Any, cfg: Any, origins: Any) -> dict[str, Any]:
    # lightgbm beats the floor for GOOD, loses for BAD.
    skill = 0.5 if cfg.target_code == "GOOD" else -0.2
    return {"skill": skill, "model_run_id": f"run-{cfg.model}"}


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "run_tune", _fake_tune)
    monkeypatch.setattr(sel, "run_backtest", _fake_backtest)


def test_picks_winning_candidate() -> None:
    entry = sel.select_series_model(
        _DATA,
        "GOOD",
        "DE",
        ORIGINS,
        horizon_days=30,
        candidates=["lightgbm"],
        n_trials=3,
        track=False,
        today="2026-06-29",
    )
    assert entry["model"] == "lightgbm"
    assert entry["params"] == {"n_estimators": 123}  # tuned params carried through
    assert entry["skill"] == 0.5
    assert entry["model_run_id"] == "run-lightgbm"
    assert entry["area"] == "DE"


def test_falls_back_to_seasonal_floor() -> None:
    entry = sel.select_series_model(
        _DATA,
        "BAD",
        "FR",
        ORIGINS,
        horizon_days=30,
        candidates=["lightgbm"],
        n_trials=3,
        track=False,
        today="2026-06-29",
    )
    assert entry["model"] == "seasonal_naive"  # nothing beat the floor (skill <= 0)
    assert entry["skill"] == 0.0
    assert entry["model_run_id"] == "seasonal_naive-2026-06-29"
