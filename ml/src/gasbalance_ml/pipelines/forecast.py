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
from gasbalance_ml.pipelines.run import DataSource

log = logging.getLogger(__name__)

# Forecast layers overlaid on the climatology base, LOW -> HIGH priority (the later overlay
# wins): EC_46 (46-day extended) refines ~46d, EC_AIFS_ENS (AI ensemble, ~15d) refines the
# front. Order matters — apply EC_46 first so AIFS ENS overwrites it on its shorter window.
_FORECAST_MODELS = ("EC_46", "EC_AIFS_ENS")


def build_scenario_driver(
    data: DataSource, area: str, scenario_model: str, origin: pd.Timestamp
) -> pd.Series:
    """The blended temperature driver for one (area, scenario) at `origin`, by priority:

      actual      : `KP.TEMP.<area>`, dates < origin
      AIFS ENS    : `KP.TEMPFC.<area>.EC_AIFS_ENS`, latest vintage <= origin (~15d, AI ensemble)
      EC 46       : `KP.TEMPFC.<area>.EC_46`, latest vintage <= origin (~46d extended)
      normal / yr : `KP.TEMPLT.<area>.<scenario_model>` for the rest of the horizon
                    (MEAN = normal, REF_<year> = a weather-year replay)

    The climatology spans the whole future (full horizon coverage); each forecast overlays it
    on the dates it covers, highest fidelity winning (AIFS ENS over EC 46 over climatology). A
    missing layer is simply skipped, degrading gracefully toward climatology.
    """
    t0 = pd.Timestamp(origin).normalize()
    # Guard each read: an absent series is empty with a RangeIndex, so date-comparison
    # filtering must be skipped (it would raise on the int index).
    actual = data.read_daily_actual(f"KP.TEMP.{area}")
    history = actual[actual.index < t0] if not actual.empty else actual

    climatology = data.read_daily_actual(f"KP.TEMPLT.{area}.{scenario_model}")
    future = climatology[climatology.index >= t0].copy() if not climatology.empty else climatology

    for fc_model in _FORECAST_MODELS:  # low -> high priority; the later overlay wins
        fc = data.read_daily_vintage(f"KP.TEMPFC.{area}.{fc_model}", t0)
        if not fc.empty and not future.empty:
            future.update(fc[fc.index >= t0])

    driver = pd.concat([history, future]).sort_index()
    return driver[~driver.index.duplicated(keep="first")]


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
