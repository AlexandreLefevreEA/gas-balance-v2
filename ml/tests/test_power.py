"""GTP / Pirineos covariate-driven forecasts: produce a full-horizon path when the covariates
are present, degrade to nothing (-> a surfaced gap) when they're absent, and dispatch by family.

Numeric correctness of the spark/lignite economics needs an end-to-end check against the live DB
(zone/naming conventions); these are structural smoke + degradation tests with synthetic data.
"""

from __future__ import annotations

import pandas as pd

from gasbalance_ml.pipelines.power import (
    _forecast_gtp,
    _forecast_pirineos,
    _residual_load,
    generate_covariate_forecasts,
)
from gasbalance_ml.plan import PlanRow

ORIGIN = pd.Timestamp("2026-06-01")
HIST = pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN - pd.Timedelta(days=1))
FULL = pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN + pd.Timedelta(days=400))
FUT = pd.date_range(ORIGIN, ORIGIN + pd.Timedelta(days=400))


def _ramp(idx: pd.DatetimeIndex, base: float) -> pd.Series:
    return pd.Series([base + (i % 30) for i in range(len(idx))], index=idx, dtype=float)


class FakePower:
    """Synthetic covariates for every GTP / Pirineos code. `present=False` -> everything empty."""

    def __init__(self, present: bool = True) -> None:
        self.present = present

    def read_target(self, code: str) -> pd.Series:
        return _ramp(HIST, 50.0)

    def read_daily_actual(self, code: str) -> pd.Series:
        if not self.present:
            return pd.Series(dtype=float)
        if "LT." in code:  # long-term climatology spans the whole future
            return _ramp(FULL, 100.0 if "LOAD" in code else 20.0)
        bases = {
            "KP.LOAD": 100.0,
            "KP.GEN": 20.0,
            "KP.SPOT": 50.0,
            "KP.GASSPOT.PVB": 30.0,
            "KP.GASSPOT.PEG": 25.0,
            "KP.GASSPOT": 30.0,
            "KP.CARBON": 80.0,
            "KP.AVAIL": 5000.0,
        }
        base = next((v for k, v in bases.items() if code.startswith(k)), 1.0)
        return _ramp(HIST, base)

    def read_daily_vintage(self, code: str, origin: pd.Timestamp) -> pd.Series:
        if not self.present:
            return pd.Series(dtype=float)
        return _ramp(FUT, 26.0 if "PEG" in code else 50.0)


def test_residual_load_is_load_minus_renewables() -> None:
    resid = _residual_load(FakePower(), "DE", "MEAN", ORIGIN)
    assert not resid.empty and resid.name == "residual_load"
    # load 100-ramp minus three 20-ramp renewables -> ~40-ramp (well above zero, below load).
    sample = resid.loc[resid.index < ORIGIN].iloc[-1]
    assert 0.0 < sample < 100.0


def test_gtp_produces_full_horizon_per_scenario() -> None:
    out = _forecast_gtp(FakePower(), "CE.56", "DE", ["MEAN", "REF_2020"], ORIGIN, horizon_days=60)
    assert set(out) == {"MEAN", "REF_2020"}
    for path in out.values():
        assert not path.empty
        assert path.index.min() >= ORIGIN  # forecasts the horizon, not history


def test_gtp_degrades_to_empty_without_covariates() -> None:
    out = _forecast_gtp(FakePower(present=False), "CE.56", "DE", ["MEAN"], ORIGIN, horizon_days=60)
    assert out == {}  # no covariates -> no GTP -> close_balance surfaces the EU.DEMAND gap


def test_pirineos_spread_regression() -> None:
    out = _forecast_pirineos(FakePower(), "CE.8.6", ["MEAN", "REF_2020"], ORIGIN, horizon_days=60)
    assert set(out) == {"MEAN", "REF_2020"}
    assert not out["MEAN"].empty
    # weather-blind: identical path under every scenario.
    assert out["MEAN"].equals(out["REF_2020"])

    absent = _forecast_pirineos(
        FakePower(present=False), "CE.8.6", ["MEAN"], ORIGIN, horizon_days=60
    )
    assert absent == {}


def test_generate_covariate_forecasts_dispatches_and_skips_static() -> None:
    plan = [
        PlanRow("CE.56", "DE GTP", "DE", "gtp"),
        PlanRow("CE.8.6", "Pirineos", None, "pirineos"),
        PlanRow("CE.32", "DE Prod", "DE", "average_plus_outage"),  # static -> handled elsewhere
    ]
    rows = generate_covariate_forecasts(FakePower(), plan, ["MEAN"], ORIGIN, horizon_days=30)
    codes = {r["series_code"] for r in rows}
    assert codes == {"CE.56", "CE.8.6"}  # production is not a covariate family here
    keys = {"series_code", "target_date", "scenario", "model_run_id", "made_on", "value"}
    assert all(set(r) == keys for r in rows)


if __name__ == "__main__":
    test_residual_load_is_load_minus_renewables()
    test_gtp_produces_full_horizon_per_scenario()
    test_gtp_degrades_to_empty_without_covariates()
    test_pirineos_spread_regression()
    test_generate_covariate_forecasts_dispatches_and_skips_static()
    print("ok")
