"""build_scenario_driver: actual -> AIFS ENS -> EC 46 -> normal/weather-year, by priority."""

from __future__ import annotations

import pandas as pd

from gasbalance_ml.pipelines.forecast import build_scenario_driver

ORIGIN = pd.Timestamp("2026-06-01")


class FakeTemps:
    """Distinct value per source so each layer of the stitched blend is identifiable.

    actual (KP.TEMP.*) = 1.0 (history only), AIFS ENS = 2.0 (15d), EC 46 = 3.0 (46d),
    climatology (KP.TEMPLT.*) = 5.0 (full future range).
    """

    aifs_days = 15
    ec46_days = 46

    def read_target(self, code: str) -> pd.Series:  # unused by build_scenario_driver
        return pd.Series(dtype=float)

    def read_daily_actual(self, code: str) -> pd.Series:
        if code.startswith("KP.TEMPLT."):
            idx = pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN + pd.Timedelta(days=800))
            return pd.Series(5.0, index=idx)
        idx = pd.date_range(ORIGIN - pd.Timedelta(days=400), ORIGIN - pd.Timedelta(days=1))
        return pd.Series(1.0, index=idx)

    def read_daily_vintage(self, code: str, origin: pd.Timestamp) -> pd.Series:
        if "EC_AIFS_ENS" in code:
            idx = pd.date_range(ORIGIN, ORIGIN + pd.Timedelta(days=self.aifs_days - 1))
            return pd.Series(2.0, index=idx)
        if "EC_46" in code:
            idx = pd.date_range(ORIGIN, ORIGIN + pd.Timedelta(days=self.ec46_days - 1))
            return pd.Series(3.0, index=idx)
        return pd.Series(dtype=float)


class FakeNoForecast(FakeTemps):
    def read_daily_vintage(self, code: str, origin: pd.Timestamp) -> pd.Series:
        return pd.Series(dtype=float)  # no AIFS / EC_46 vintage available


def test_blend_layers_by_priority() -> None:
    driver = build_scenario_driver(FakeTemps(), "DE", "MEAN", ORIGIN)

    assert driver.index.is_monotonic_increasing
    assert not driver.index.has_duplicates
    history = driver[driver.index < ORIGIN]
    aifs = driver[(driver.index >= ORIGIN) & (driver.index < ORIGIN + pd.Timedelta(days=15))]
    ec46 = driver[
        (driver.index >= ORIGIN + pd.Timedelta(days=15))
        & (driver.index < ORIGIN + pd.Timedelta(days=46))
    ]
    tail = driver[driver.index >= ORIGIN + pd.Timedelta(days=46)]

    assert (history == 1.0).all() and not history.empty  # actuals
    assert (aifs == 2.0).all() and not aifs.empty  # AIFS ENS wins the front (~15d)
    assert (ec46 == 3.0).all() and not ec46.empty  # EC 46 fills 15-46d
    assert (tail == 5.0).all() and not tail.empty  # normal/weather-year climatology tail


def test_falls_back_to_climatology_without_forecasts() -> None:
    driver = build_scenario_driver(FakeNoForecast(), "DE", "REF_2020", ORIGIN)
    future = driver[driver.index >= ORIGIN]
    assert (future == 5.0).all() and not future.empty  # pure climatology, no 2.0/3.0 anywhere
    assert (driver[driver.index < ORIGIN] == 1.0).all()
