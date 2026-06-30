"""The weather-blind supply models + forecast_static + generate_supply_forecasts.

Each model: correct projection from history, and NaN (a gap) when the source is absent.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from gasbalance_ml.models.static import (
    AbsoluteZero,
    AveragePlusOutage,
    Azeri,
    BoundedPersistence,
    Ffill,
    SeasonalMean,
)
from gasbalance_ml.pipelines.forecast import forecast_static, generate_supply_forecasts
from gasbalance_ml.plan import PlanRow

ORIGIN = pd.Timestamp("2026-06-01")
FUT = pd.DataFrame(index=pd.date_range(ORIGIN, periods=5, freq="D"))
EMPTY = pd.DataFrame(index=pd.DatetimeIndex([]))


def _hist(values: list[float]) -> pd.Series:
    idx = pd.date_range(ORIGIN - pd.Timedelta(days=len(values)), periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


def test_absolute_zero() -> None:
    m = AbsoluteZero()
    m.fit(_hist([5.0, 6.0]), EMPTY)
    assert (m.predict(FUT) == 0.0).all()


def test_ffill_last_value_and_nan_when_empty() -> None:
    m = Ffill()
    m.fit(_hist([1.0, 2.0, 3.0]), EMPTY)
    assert (m.predict(FUT) == 3.0).all()

    empty = Ffill()
    empty.fit(pd.Series(dtype=float), EMPTY)
    assert empty.predict(FUT).isna().all()  # no source -> gap


def test_average_plus_outage_trailing_mean() -> None:
    m = AveragePlusOutage(window_days=3)
    m.fit(_hist([10.0, 20.0, 30.0, 40.0, 50.0]), EMPTY)  # last 3 -> (30+40+50)/3 = 40
    assert (m.predict(FUT) == 40.0).all()


def test_seasonal_mean_by_day_of_year() -> None:
    # Two years; same calendar day differs -> the model averages them by day-of-year.
    idx = pd.DatetimeIndex([dt.date(2024, 6, 1), dt.date(2025, 6, 1), dt.date(2024, 6, 2)])
    m = SeasonalMean()
    m.fit(pd.Series([10.0, 30.0, 7.0], index=idx), EMPTY)
    future = pd.DataFrame(index=pd.DatetimeIndex([dt.date(2026, 6, 1), dt.date(2026, 6, 2)]))
    out = m.predict(future)
    assert out.iloc[0] == 20.0  # mean(10, 30) for Jun 1
    assert out.iloc[1] == 7.0


def test_azeri_and_bounded_persistence() -> None:
    a = Azeri(window_days=2)
    a.fit(_hist([100.0, 10.0, 20.0]), EMPTY)  # last 2 -> 15
    assert (a.predict(FUT) == 15.0).all()

    b = BoundedPersistence(floor=0.0)
    b.fit(_hist([5.0, -3.0]), EMPTY)  # last value -3 -> floored at 0
    assert (b.predict(FUT) == 0.0).all()


def test_forecast_static_uses_pre_origin_history_and_full_horizon() -> None:
    target = pd.Series(
        2.0, index=pd.date_range(ORIGIN - pd.Timedelta(days=10), ORIGIN + pd.Timedelta(days=10))
    )
    out = forecast_static(Ffill(), target, ORIGIN, horizon_days=7)
    assert len(out) == 7 and out.index[0] == ORIGIN
    assert (out == 2.0).all()  # fit on history < origin (post-origin actuals ignored)

    nan_out = forecast_static(Ffill(), pd.Series(dtype=float), ORIGIN, horizon_days=7)
    assert nan_out.isna().all()


class _FakeData:
    """read_target: a present production series and an absent LNG series."""

    def read_target(self, code: str) -> pd.Series:
        if code == "CE.PRESENT":
            return pd.Series(50.0, index=pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN))
        return pd.Series(dtype=float)  # CE.ABSENT -> no data

    def read_daily_actual(self, code: str) -> pd.Series:  # unused here
        return pd.Series(dtype=float)

    def read_daily_vintage(self, code: str, origin: pd.Timestamp) -> pd.Series:
        return pd.Series(dtype=float)


def test_generate_supply_forecasts_emits_per_scenario_and_nan_for_absent() -> None:
    plan = [
        PlanRow("CE.PRESENT", "NL Prod", "NL", "average_plus_outage"),
        PlanRow("CE.ABSENT", "GR LNG", "GR", "seasonal_mean"),
        PlanRow("CE.54", "DE LDZ", "DE", "demand"),  # covariate-driven: skipped here
    ]
    rows = generate_supply_forecasts(
        _FakeData(), plan, ["MEAN", "REF_2020"], ORIGIN, horizon_days=3
    )
    codes = {r["series_code"] for r in rows}
    assert codes == {"CE.PRESENT", "CE.ABSENT"}  # demand not handled by the static path

    present = [r for r in rows if r["series_code"] == "CE.PRESENT"]
    assert {r["scenario"] for r in present} == {"MEAN", "REF_2020"}
    assert all(r["value"] == 50.0 for r in present)  # trailing mean of a flat 50 series

    absent = [r for r in rows if r["series_code"] == "CE.ABSENT"]
    assert absent and all(pd.isna(r["value"]) for r in absent)  # emitted as NaN -> gap downstream


if __name__ == "__main__":
    test_absolute_zero()
    test_ffill_last_value_and_nan_when_empty()
    test_average_plus_outage_trailing_mean()
    test_seasonal_mean_by_day_of_year()
    test_azeri_and_bounded_persistence()
    test_forecast_static_uses_pre_origin_history_and_full_horizon()
    test_generate_supply_forecasts_emits_per_scenario_and_nan_for_absent()
    print("ok")
