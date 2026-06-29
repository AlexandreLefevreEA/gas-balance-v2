"""Rolling-origin (walk-forward) backtest — pure: model + assemble injected, no DB.

For each origin T: assemble the leakage-safe (train, future) matrices, fit a fresh model,
predict the horizon, and record (origin, target_date, horizon, y_pred, y_true). `y_true`
is the realized actual where known (NaN past the actuals — a pure forecast). Metrics are
computed downstream from the returned frame (`evaluation.metrics`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import pandas as pd

from gasbalance_ml.features.assemble import assemble
from gasbalance_ml.models.base import Model


def walk_forward(
    target: pd.Series,
    driver: pd.Series | Callable[[pd.Timestamp], pd.Series],
    model_factory: Callable[[], Model],
    origins: Sequence[pd.Timestamp],
    horizon_days: int,
    *,
    window: str = "expanding",
    sliding_years: int = 5,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for origin in origins:
        t0 = pd.Timestamp(origin).normalize()
        # `driver` is either a fixed daily series (actual / scenario, perfect foresight)
        # or a per-origin resolver (vintage = the forecast known at t0). Same harness.
        temp = driver(t0) if callable(driver) else driver
        a = assemble(target, temp, t0, horizon_days, window=window, sliding_years=sliding_years)
        if a.y_train.empty or a.X_future.empty:
            continue
        model = model_factory()
        model.fit(a.y_train, a.X_train)
        yhat = model.predict(a.X_future)
        for d, p in yhat.items():
            true = target.get(d)
            rows.append(
                {
                    "origin": t0,
                    "target_date": d,
                    "horizon": int((d - t0).days),
                    "y_pred": float(p),
                    "y_true": float(true) if true is not None and pd.notna(true) else float("nan"),
                }
            )
    return pd.DataFrame(rows, columns=["origin", "target_date", "horizon", "y_pred", "y_true"])


def predict_forward(
    model: Model,
    target: pd.Series,
    driver: pd.Series | Callable[[pd.Timestamp], pd.Series],
    origin: pd.Timestamp,
    horizon_days: int,
    *,
    window: str = "expanding",
    sliding_years: int = 5,
) -> pd.Series:
    """Fit `model` on history < origin and predict [origin, origin+H) — the inference seam
    the running/balance layer calls. Feed a REF-year series as `driver` for a weather
    scenario; this writes nothing, it just returns the predicted Series.
    """
    t0 = pd.Timestamp(origin).normalize()
    temp = driver(t0) if callable(driver) else driver
    a = assemble(target, temp, t0, horizon_days, window=window, sliding_years=sliding_years)
    model.fit(a.y_train, a.X_train)
    return model.predict(a.X_future)


def predict_scenarios(
    model: Model,
    target: pd.Series,
    drivers: Mapping[str, pd.Series],
    origin: pd.Timestamp,
    horizon_days: int,
    *,
    window: str = "expanding",
    sliding_years: int = 5,
) -> dict[str, pd.Series]:
    """Fit `model` once on history < origin, then predict [origin, origin+H) under each
    scenario driver. Returns {scenario: predicted Series}.

    The training matrix is scenario-independent — a weather scenario only changes *future*
    temps, the history is the same actuals — so we fit ONCE and predict many. That's the
    fan-out's hot path: N series x M scenarios costs N fits (the expensive part), not N*M.
    A weather-blind model (e.g. seasonal_naive) naturally returns the same path for every
    scenario, since its prediction ignores the driver.
    """
    t0 = pd.Timestamp(origin).normalize()
    if not drivers:
        return {}
    # Train on any scenario's driver — they share the pre-origin history used for X_train.
    any_driver = next(iter(drivers.values()))
    trained = assemble(
        target, any_driver, t0, horizon_days, window=window, sliding_years=sliding_years
    )
    model.fit(trained.y_train, trained.X_train)
    out: dict[str, pd.Series] = {}
    for scenario, driver in drivers.items():
        a = assemble(target, driver, t0, horizon_days, window=window, sliding_years=sliding_years)
        out[scenario] = model.predict(a.X_future)
    return out
