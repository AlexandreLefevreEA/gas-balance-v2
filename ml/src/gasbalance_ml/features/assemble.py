"""Assemble leakage-safe (train, predict) feature matrices for one forecast origin.

THE single place leakage is enforced. Given a daily target, a daily driver series, an
origin T and a horizon, it returns:
  - y_train, X_train : training rows **strictly before T** (the cut),
  - X_future         : feature rows for the horizon [T, T+H).

Features are pointwise in the date (calendar) and in the driver (HDD/CDD) — no target
autoregression and no centered/rolling windows — so a training feature can never read a
value at or after T. The driver series is whatever the data layer supplied for the chosen
covariate_mode (actual / vintage / scenario); the assembler is mode-agnostic.

ponytail: no target lags in v1 (HDD + calendar already carries LDZ demand). Lags and
recursion are the leakage-prone part — add them with explicit origin-known guards only
when they measurably help.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from gasbalance_ml.features.calendar import calendar_features, degree_days


@dataclass(frozen=True)
class Assembled:
    y_train: pd.Series
    X_train: pd.DataFrame
    X_future: pd.DataFrame


def _features(index: pd.DatetimeIndex, temp_daily: pd.Series) -> pd.DataFrame:
    cal = calendar_features(index)
    dd = degree_days(temp_daily.reindex(index))
    return pd.concat([cal, dd], axis=1)


def assemble(
    target: pd.Series,
    temp_daily: pd.Series,
    origin: pd.Timestamp,
    horizon_days: int,
    *,
    window: str = "expanding",
    sliding_years: int = 5,
) -> Assembled:
    origin = pd.Timestamp(origin).normalize()
    target = target.sort_index()

    # --- training: STRICTLY before the origin (the leakage cut) ---
    train_idx = target.index[target.index < origin]
    if window == "sliding":
        start = origin - pd.DateOffset(years=sliding_years)
        train_idx = train_idx[train_idx >= start]
    y_train = target.loc[train_idx].dropna()
    X_train = _features(pd.DatetimeIndex(y_train.index), temp_daily)
    keep = X_train.notna().all(axis=1)  # drop days with no driver (e.g. missing temp)
    X_train, y_train = X_train.loc[keep], y_train.loc[keep]

    # --- prediction: the horizon [T, T+H) ---
    future_idx = pd.date_range(origin, periods=horizon_days, freq="D")
    X_future = _features(future_idx, temp_daily)
    X_future = X_future.loc[X_future.notna().all(axis=1)]  # only dates the driver covers

    return Assembled(y_train=y_train, X_train=X_train, X_future=X_future)
