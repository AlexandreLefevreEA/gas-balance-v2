"""The `forecast` step — run every series under every weather scenario, collect the rows.

Per series: instantiate its registry model, fit ONCE on history, then predict each scenario
(fit-once / predict-many — `predict_scenarios`). A weather scenario is a temperature path:
actual history, then the latest near-term forecast (`EC_46`, ~46d), then the scenario's
long-term climatology (`MEAN` / `REF_<year>`) for the tail (`build_scenario_driver`). Drivers
are cached per `(area, scenario)` since many series share an area.

This module is pure orchestration over the read-only `DataSource` + the registry; writing the
rows to Postgres is `publish.publish_forecasts`. So `generate_forecasts` is unit-testable with
a fake reader, no DB (ml/tests/test_forecast_publish.py).
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from collections.abc import Sequence
from typing import Any

import pandas as pd

from gasbalance_ml.evaluation.walkforward import predict_scenarios
from gasbalance_ml.models import get
from gasbalance_ml.models.base import Model
from gasbalance_ml.pipelines.run import DataSource
from gasbalance_ml.plan import PlanRow

log = logging.getLogger(__name__)

# Static (weather-blind) families forecast from their own history -> family : registered model.
_STATIC_MODEL: dict[str, str] = {
    "absolute_zero": "absolute_zero",
    "average_plus_outage": "average_plus_outage",
    "seasonal_mean": "seasonal_mean",
    "azeri": "azeri",
    "ffill": "ffill",
    "bounded_persistence": "bounded_persistence",
}

# Forecast layers overlaid on the climatology base, LOW -> HIGH priority (the later overlay
# wins): EC_46 (46-day extended) refines ~46d, EC_AIFS_ENS (AI ensemble, ~15d) refines the
# front. Order matters — apply EC_46 first so AIFS ENS overwrites it on its shorter window.
_FORECAST_MODELS = ("EC_46", "EC_AIFS_ENS")


def build_covariate_driver(
    data: DataSource,
    origin: pd.Timestamp,
    *,
    actual: str | None,
    forecasts: Sequence[str] = (),
    climatology: str | None = None,
) -> pd.Series:
    """Blend one covariate into a daily driver over history+future at `origin`, by priority:

      actual      : `actual` code, dates < origin (the realized history)
      forecasts   : each `forecasts` vintage, latest <= origin, overlaid LOW->HIGH priority
                    (later wins on its window — e.g. [EC_46, EC_AIFS_ENS])
      climatology : `climatology` code spanning the whole future (the fallback tail)

    Any layer whose code is absent/empty is simply skipped, degrading gracefully toward the
    climatology (and, if that too is absent, toward just the history). The single blend used
    by every covariate-driven family (temperature, load, generation, prices, …)."""
    t0 = pd.Timestamp(origin).normalize()
    # Guard each read: an absent series is empty with a RangeIndex, so date-comparison
    # filtering must be skipped (it would raise on the int index).
    history = pd.Series(dtype=float)
    if actual:
        a = data.read_daily_actual(actual)
        history = a[a.index < t0] if not a.empty else a

    future = pd.Series(dtype=float)
    if climatology:
        c = data.read_daily_actual(climatology)
        future = c[c.index >= t0].copy() if not c.empty else c

    for code in forecasts:  # low -> high priority; the later overlay wins
        fc = data.read_daily_vintage(code, t0)
        if fc.empty:
            continue
        fc = fc[fc.index >= t0]
        # With a climatology canvas, overlay onto it (its dates only). Without one (e.g. a price
        # forward curve, which IS the future), the first forecast becomes the future canvas.
        if future.empty:
            future = fc.copy()
        else:
            future.update(fc)

    driver = pd.concat([history, future]).sort_index()
    return driver[~driver.index.duplicated(keep="first")]


def build_scenario_driver(
    data: DataSource, area: str, scenario_model: str, origin: pd.Timestamp
) -> pd.Series:
    """The blended temperature driver for one (area, scenario): actual `KP.TEMP.<area>`, then
    `KP.TEMPFC.<area>.{EC_46,EC_AIFS_ENS}` vintages (AIFS ENS wins the front ~15d, EC 46 fills
    to ~46d), then `KP.TEMPLT.<area>.<scenario_model>` climatology for the tail (MEAN = normal,
    REF_<year> = a weather-year replay). A thin wrapper over `build_covariate_driver`."""
    return build_covariate_driver(
        data,
        origin,
        actual=f"KP.TEMP.{area}",
        forecasts=[f"KP.TEMPFC.{area}.{m}" for m in _FORECAST_MODELS],
        climatology=f"KP.TEMPLT.{area}.{scenario_model}",
    )


def generate_forecasts(
    data: DataSource,
    registry: dict[str, dict[str, Any]],
    scenarios: Sequence[str],
    origin: pd.Timestamp,
    *,
    horizon_days: int = 720,
    made_on: dt.date | None = None,
    window: str = "expanding",
    sliding_years: int = 5,
) -> list[dict[str, Any]]:
    """Forecast every registry series across every scenario at `origin`. Returns forecast rows
    `{series_code, target_date, scenario, model_run_id, made_on, value}` (finite values only)."""
    t0 = pd.Timestamp(origin).normalize()
    stamp = made_on or t0.date()
    driver_cache: dict[tuple[str, str], pd.Series] = {}
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []

    for code, entry in registry.items():
        area = entry["area"]
        model_name = entry["model"]
        params = entry.get("params") or {}
        model_run_id = entry.get("model_run_id") or f"{model_name}-{stamp}"

        target = data.read_target(code)
        if target.empty:
            skipped.append(code)
            continue

        drivers: dict[str, pd.Series] = {}
        for scenario in scenarios:
            key = (area, scenario)
            if key not in driver_cache:
                driver_cache[key] = build_scenario_driver(data, area, scenario, t0)
            drivers[scenario] = driver_cache[key]

        model = get(model_name)(**params)
        preds = predict_scenarios(
            model, target, drivers, t0, horizon_days, window=window, sliding_years=sliding_years
        )
        for scenario, series in preds.items():
            for date, value in series.items():
                if not math.isfinite(value):
                    continue
                rows.append(
                    {
                        "series_code": code,
                        "target_date": pd.Timestamp(date).date(),
                        "scenario": scenario,
                        "model_run_id": model_run_id,
                        "made_on": stamp,
                        "value": float(value),
                    }
                )
    if skipped:
        log.info("forecast: skipped %d/%d series with no actuals", len(skipped), len(registry))
        log.debug("forecast: skipped %s", ", ".join(skipped))
    log.info(
        "forecast: %d rows (%d series x %d scenarios)", len(rows), len(registry), len(scenarios)
    )
    return rows


def forecast_static(
    model: Model, target: pd.Series, origin: pd.Timestamp, horizon_days: int
) -> pd.Series:
    """Fit a weather-blind model on history < origin and predict [origin, origin+H) over a bare
    date index (these models read only `X.index`). Empty history -> the model returns all-NaN."""
    t0 = pd.Timestamp(origin).normalize()
    history = target[target.index < t0] if not target.empty else target
    model.fit(history, pd.DataFrame(index=history.index))
    future_idx = pd.date_range(t0, periods=horizon_days, freq="D")
    return model.predict(pd.DataFrame(index=future_idx))


def generate_supply_forecasts(
    data: DataSource,
    plan: Sequence[PlanRow],
    scenarios: Sequence[str],
    origin: pd.Timestamp,
    *,
    horizon_days: int = 720,
    made_on: dt.date | None = None,
) -> list[dict[str, Any]]:
    """Forecast the static supply families in `plan` (production / LNG sendout / linepack /
    imbalance / capacity / Azeri), each projected from its own history by its family's model and
    replicated across every scenario (weather-blind). Emits NaN cells where a source series is
    absent so `close_balance` surfaces the gap; the publish boundary drops the NaN cells."""
    t0 = pd.Timestamp(origin).normalize()
    stamp = made_on or t0.date()
    rows: list[dict[str, Any]] = []
    n_series = 0
    for row in plan:
        model_name = _STATIC_MODEL.get(row.family)
        if model_name is None:
            continue  # demand / gtp / pirineos / moffat are covariate-driven (handled elsewhere)
        n_series += 1
        path = forecast_static(get(model_name)(), data.read_target(row.code), t0, horizon_days)
        model_run_id = f"{model_name}-{stamp}"
        for date, value in path.items():
            for scenario in scenarios:
                rows.append(
                    {
                        "series_code": row.code,
                        "target_date": pd.Timestamp(date).date(),
                        "scenario": scenario,
                        "model_run_id": model_run_id,
                        "made_on": stamp,
                        "value": float(value),
                    }
                )
    log.info("supply: %d static series x %d scenarios", n_series, len(scenarios))
    return rows
