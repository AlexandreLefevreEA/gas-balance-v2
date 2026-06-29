"""Backtest orchestration check with an in-memory fake reader — no DB, no MLflow."""

from __future__ import annotations

import numpy as np
import pandas as pd

from gasbalance_ml.pipelines.run import Config, run_backtest


class FakeData:
    """Synthetic HDD-driven demand + temperature (readers only)."""

    def __init__(self) -> None:
        idx = pd.date_range("2018-01-01", periods=1600, freq="D")
        self.temp = pd.Series(10 + 10 * np.sin(2 * np.pi * idx.dayofyear / 365.25), index=idx)
        self.target = 100 - 5 * (15.5 - self.temp).clip(lower=0)

    def read_target(self, code: str) -> pd.Series:
        return self.target

    def read_daily_actual(self, code: str) -> pd.Series:
        return self.temp

    def read_daily_vintage(self, code: str, origin: pd.Timestamp) -> pd.Series:
        return self.temp[self.temp.index <= origin]  # crude point-in-time slice


def test_backtest_pipeline_reports_surface_and_skill() -> None:
    data = FakeData()
    cfg = Config(
        target_code="X",
        horizon_days=60,
        model="seasonal_naive",
        mode="actual",
        actual_temp_code="T",
        track=False,
    )
    origins = [pd.Timestamp("2021-01-01"), pd.Timestamp("2021-03-01")]
    out = run_backtest(data, cfg, origins)
    assert not out["surface"].empty
    assert out["mae"] >= 0
    assert abs(out["skill"]) < 1e-9  # model == baseline (both seasonal_naive) -> zero skill
