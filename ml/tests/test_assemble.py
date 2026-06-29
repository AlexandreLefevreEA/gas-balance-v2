"""The leakage cut: training features must never read a value at/after the origin."""

from __future__ import annotations

import pandas as pd

from gasbalance_ml.features.assemble import assemble


def _series(start: str, n: int) -> pd.Series:
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.Series(range(n), index=idx, dtype=float)


def test_train_strictly_before_origin() -> None:
    target = _series("2020-01-01", 800)
    temp = _series("2020-01-01", 1200)  # driver covers the horizon
    origin = pd.Timestamp("2022-01-01")
    a = assemble(target, temp, origin, horizon_days=365)
    assert not a.X_train.empty
    assert a.X_train.index.max() < origin  # the cut
    assert a.y_train.index.max() < origin
    assert a.X_future.index.min() >= origin  # horizon starts at the origin, never before


def test_future_target_cannot_change_features() -> None:
    # Mutating target values at/after the origin must not change any assembled feature.
    target = _series("2020-01-01", 800)
    temp = _series("2020-01-01", 1200)
    origin = pd.Timestamp("2022-01-01")
    a1 = assemble(target, temp, origin, 365)
    poisoned = target.copy()
    poisoned.loc[poisoned.index >= origin] = -999.0
    a2 = assemble(poisoned, temp, origin, 365)
    pd.testing.assert_frame_equal(a1.X_train, a2.X_train)
    pd.testing.assert_frame_equal(a1.X_future, a2.X_future)


def test_sliding_window_bounds_training() -> None:
    target = _series("2015-01-01", 3000)
    temp = _series("2015-01-01", 3400)
    origin = pd.Timestamp("2023-01-01")
    a = assemble(target, temp, origin, 90, window="sliding", sliding_years=3)
    assert a.X_train.index.min() >= origin - pd.DateOffset(years=3)
    assert a.X_train.index.max() < origin
