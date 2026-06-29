"""predict_scenarios fits once; generate_forecasts emits correctly-keyed finite rows."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from gasbalance_ml.evaluation.walkforward import predict_scenarios
from gasbalance_ml.models.base import Model
from gasbalance_ml.pipelines.forecast import generate_forecasts

ORIGIN = pd.Timestamp("2024-06-01")


class SpyModel(Model):
    name = "spy"

    def __init__(self) -> None:
        self.fits = 0

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        self.fits += 1

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=X.index, dtype=float)


class FakeData:
    """Constant demand + temps; full future climatology, 46d near-term, history actuals."""

    def read_target(self, code: str) -> pd.Series:
        idx = pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN - pd.Timedelta(days=1))
        return pd.Series(100.0, index=idx)

    def read_daily_actual(self, code: str) -> pd.Series:
        if code.startswith("KP.TEMPLT."):
            idx = pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN + pd.Timedelta(days=400))
            return pd.Series(5.0, index=idx)
        idx = pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN - pd.Timedelta(days=1))
        return pd.Series(5.0, index=idx)

    def read_daily_vintage(self, code: str, origin: pd.Timestamp) -> pd.Series:
        idx = pd.date_range(ORIGIN, ORIGIN + pd.Timedelta(days=45))
        return pd.Series(4.0, index=idx)


def test_predict_scenarios_fits_once_predicts_per_scenario() -> None:
    target = pd.Series(
        100.0, index=pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN - pd.Timedelta(days=1))
    )
    temp_idx = pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN + pd.Timedelta(days=120))
    temp = pd.Series(5.0, index=temp_idx)
    drivers = {"MEAN": temp, "REF_2020": temp}

    model = SpyModel()
    out = predict_scenarios(model, target, drivers, ORIGIN, 90)

    assert model.fits == 1  # the whole point: fit once, predict many
    assert set(out) == {"MEAN", "REF_2020"}
    assert not out["MEAN"].empty


def test_generate_forecasts_rows_and_weather_blind_invariance() -> None:
    registry = {"X": {"area": "DE", "model": "seasonal_naive", "params": {}, "model_run_id": "mr1"}}
    scenarios = ["MEAN", "REF_2020"]

    rows = generate_forecasts(FakeData(), registry, scenarios, ORIGIN, horizon_days=90)

    assert rows
    keys = {"series_code", "target_date", "scenario", "model_run_id", "made_on", "value"}
    assert all(set(r) == keys for r in rows)
    assert {r["series_code"] for r in rows} == {"X"}
    assert {r["model_run_id"] for r in rows} == {"mr1"}
    assert {r["made_on"] for r in rows} == {ORIGIN.date()}
    assert {r["scenario"] for r in rows} == {"MEAN", "REF_2020"}
    assert all(isinstance(r["target_date"], dt.date) for r in rows)
    assert all(pd.notna(r["value"]) for r in rows)

    # seasonal_naive ignores the driver -> every scenario yields the same path per target_date.
    def path(scenario: str) -> dict[object, float]:
        return {r["target_date"]: r["value"] for r in rows if r["scenario"] == scenario}

    assert path("MEAN") == path("REF_2020")
