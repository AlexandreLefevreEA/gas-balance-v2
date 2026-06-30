"""Covariate-driven forecasts that aren't temperature-demand: GTP (gas-for-power), Pirineos
(Iberian gas spread) and Moffat (demand-like). Ports legacy's power-sector + spread models with
fast tools (LightGBM, no darts), reusing `build_covariate_driver` for the actual->forecast->
long-term blend.

Weather sensitivity: GTP's residual load and Moffat's temperature shift per weather scenario
(fit-once / predict-many over the replays); the Pirineos gas spread does not (one path reused).

Robustness: every covariate read degrades gracefully — an absent code drops its feature column,
and a series that can't be built at all yields no values, which `close_balance` then surfaces as
a gap (per the NaN rule) rather than a silently-low total. NOTE: the exact covariate zone/naming
conventions (e.g. DE vs DE-LU, REF_2020 vs REF2020) need an end-to-end check against the live DB;
until then GTP relies on this graceful degradation + the covariate-presence warnings.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Any

import pandas as pd

from gasbalance_ml.evaluation.walkforward import predict_scenarios
from gasbalance_ml.features.calendar import calendar_features
from gasbalance_ml.models import get
from gasbalance_ml.pipelines.forecast import build_covariate_driver, build_scenario_driver
from gasbalance_ml.pipelines.run import DataSource
from gasbalance_ml.plan import PlanRow

log = logging.getLogger(__name__)

_FC = ("EC_46", "EC_AIFS_ENS")  # forecast-vintage models, low -> high priority
_RENEW = ("SOLAR", "WIND", "ROR")  # renewable fuels subtracted from load -> residual load
_GTP_AREA = {"GB": "DE"}  # GB GTP runs on German power covariates (no UK power spot) — legacy quirk
_SPOT_ZONE = {"DE": "DE-LU", "DK": "DK1", "IT": "IT-NORTH"}  # power-spot zone naming
_GEN_FC_ZONE = {"DE": "DE-LU"}  # generation forecast/long-term zone for DE
# Clean spark / lignite spread efficiencies (legacy GTPModel defaults).
_ETA_GAS, _ETA_CARBON, _ETA_LIGNITE = 0.5, 0.38, 1.05


def _lt(scenario: str) -> str:
    """Scenario token as written on KP.*LT codes — the underscore is dropped there
    (`REF_2020` temp scenario -> `REF2020` on load/generation long-term)."""
    return scenario.replace("REF_", "REF")


def _residual_load(data: DataSource, area: str, scenario: str, origin: pd.Timestamp) -> pd.Series:
    """Residual electricity load = total load - renewable generation (solar+wind+run-of-river),
    blended per weather scenario. The dominant, weather-sensitive driver of gas-for-power."""
    z = _GTP_AREA.get(area, area)
    gz = _GEN_FC_ZONE.get(z, z)
    load = build_covariate_driver(
        data,
        origin,
        actual=f"KP.LOAD.{z}",
        forecasts=[f"KP.LOADFC.{z}.{m}" for m in _FC],
        climatology=f"KP.LOADLT.{z}.{_lt(scenario)}",
    )
    if load.empty:
        return load
    renew = pd.Series(0.0, index=load.index)
    for fuel in _RENEW:
        g = build_covariate_driver(
            data,
            origin,
            actual=f"KP.GEN.{fuel}.{z}",
            forecasts=[f"KP.GENFC.{fuel}.{gz}.{m}" for m in _FC],
            climatology=f"KP.GENLT.{fuel}.{gz}.{_lt(scenario)}",
        )
        if not g.empty:
            renew = renew.add(g.reindex(load.index), fill_value=0.0)
    return (load - renew).rename("residual_load")


def _gtp_static_features(data: DataSource, area: str, origin: pd.Timestamp) -> pd.DataFrame:
    """The weather-independent GTP drivers: clean spark spread, clean lignite spread, gas-plant
    availability. Built once per area (the spreads/availability don't change with weather)."""
    z = _GTP_AREA.get(area, area)
    sz = _SPOT_ZONE.get(z, z)
    power = build_covariate_driver(data, origin, actual=f"KP.SPOT.{sz}", forecasts=[f"KP.PFC.{sz}"])
    cols: dict[str, pd.Series] = {}
    if not power.empty:
        gas = build_covariate_driver(
            data, origin, actual="KP.GASSPOT.TTF", forecasts=["KP.GASFC.TTF"]
        )
        carbon = build_covariate_driver(
            data, origin, actual="KP.CARBON.SPOT", forecasts=["KP.CARBON.SETTLES"]
        )
        gas = gas.reindex(power.index).ffill()
        carbon = carbon.reindex(power.index).ffill()
        cols["spark"] = power - gas / _ETA_GAS - carbon * _ETA_CARBON
        cols["lignite"] = power - carbon * _ETA_LIGNITE
    avail = build_covariate_driver(
        data, origin, actual=f"KP.AVAIL.GAS.{z}", forecasts=[f"KP.AVAILFC.GAS.{z}"]
    )
    if not avail.empty:
        cols["avail_gas"] = avail
    return pd.DataFrame(cols)


def _gtp_features(
    static: pd.DataFrame, residual: pd.Series, horizon_days: int, origin: pd.Timestamp
) -> pd.DataFrame:
    """Assemble the GTP feature frame (history + horizon): residual load + the static spread /
    availability drivers (forward-filled across the horizon) + a cyclic calendar."""
    t0 = pd.Timestamp(origin).normalize()
    parts = [p for p in (residual, static) if not p.empty]
    if not parts:
        return pd.DataFrame()
    feats = pd.concat(parts, axis=1).sort_index()
    horizon = pd.date_range(t0, periods=horizon_days, freq="D")
    full = feats.index.union(horizon)
    feats = feats.reindex(full)
    # Spreads / availability are forward-persisted across the horizon; residual load already
    # spans the future via its climatology, so it is NOT ffilled (a true gap stays a gap).
    for col in feats.columns:
        if col != "residual_load":
            feats[col] = feats[col].ffill()
    cal = calendar_features(pd.DatetimeIndex(feats.index))
    return pd.concat([feats, cal], axis=1)


def _emit(
    preds: dict[str, pd.Series],
    code: str,
    model_run_id: str,
    origin: pd.Timestamp,
    horizon_days: int,
    stamp: dt.date,
) -> list[dict[str, Any]]:
    """Reindex each scenario's prediction to the full horizon (NaN where unpredicted) and emit
    rows incl. NaN — so close_balance surfaces any gap; the publish boundary drops the NaN."""
    horizon = pd.date_range(pd.Timestamp(origin).normalize(), periods=horizon_days, freq="D")
    rows: list[dict[str, Any]] = []
    for scenario, series in preds.items():
        full = series.reindex(horizon)
        for date, value in full.items():
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
    return rows


def _forecast_gtp(
    data: DataSource,
    code: str,
    area: str,
    scenarios: Sequence[str],
    origin: pd.Timestamp,
    horizon_days: int,
) -> dict[str, pd.Series]:
    """Fit one LightGBM on history (scenario-independent), predict each weather scenario."""
    t0 = pd.Timestamp(origin).normalize()
    target = data.read_target(code)
    static = _gtp_static_features(data, area, t0)  # built once
    feats = {
        s: _gtp_features(static, _residual_load(data, area, s, t0), horizon_days, t0)
        for s in scenarios
    }
    any_X = next((X for X in feats.values() if not X.empty), pd.DataFrame())
    train_idx = target.index[target.index < t0]
    if any_X.empty or len(train_idx) == 0:
        return {}
    X_train = any_X.reindex(train_idx).dropna()
    y_train = target.reindex(X_train.index)
    X_train, y_train = X_train.loc[y_train.notna()], y_train.dropna()
    if X_train.empty:
        return {}
    model = get("lightgbm")()
    model.fit(y_train, X_train)
    out: dict[str, pd.Series] = {}
    horizon = pd.date_range(t0, periods=horizon_days, freq="D")
    for s, X in feats.items():
        X_future = X.reindex(horizon).dropna() if not X.empty else pd.DataFrame()
        out[s] = model.predict(X_future) if not X_future.empty else pd.Series(dtype=float)
    return out


def _forecast_pirineos(
    data: DataSource,
    code: str,
    scenarios: Sequence[str],
    origin: pd.Timestamp,
    horizon_days: int,
) -> dict[str, pd.Series]:
    """Regress the flow on the Spain-vs-France gas spread (PVB - PEG, forward curve for the
    future). Weather-blind -> one path reused across every scenario."""
    t0 = pd.Timestamp(origin).normalize()
    pvb = build_covariate_driver(data, t0, actual="KP.GASSPOT.PVB", forecasts=["KP.GASFC.PVB"])
    peg = build_covariate_driver(data, t0, actual="KP.GASSPOT.PEG", forecasts=["KP.GASFC.PEG"])
    if pvb.empty or peg.empty:
        return {}
    spread = (pvb - peg.reindex(pvb.index)).rename("spread").to_frame()
    feats = pd.concat([spread, calendar_features(pd.DatetimeIndex(spread.index))], axis=1)
    target = data.read_target(code)
    train_idx = target.index[target.index < t0]
    X_train = feats.reindex(train_idx).dropna()
    y_train = target.reindex(X_train.index)
    X_train, y_train = X_train.loc[y_train.notna()], y_train.dropna()
    horizon = pd.date_range(t0, periods=horizon_days, freq="D")
    X_future = feats.reindex(horizon).dropna()
    if X_train.empty or X_future.empty:
        return {}
    model = get("lightgbm")()
    model.fit(y_train, X_train)
    path = model.predict(X_future)
    return {s: path for s in scenarios}  # weather-blind: same path every scenario


def generate_covariate_forecasts(
    data: DataSource,
    plan: Sequence[PlanRow],
    scenarios: Sequence[str],
    origin: pd.Timestamp,
    *,
    horizon_days: int = 720,
    made_on: dt.date | None = None,
) -> list[dict[str, Any]]:
    """Forecast the covariate-driven families in `plan` (GTP, Pirineos, Moffat) per scenario."""
    t0 = pd.Timestamp(origin).normalize()
    stamp = made_on or t0.date()
    rows: list[dict[str, Any]] = []
    counts = {"gtp": 0, "pirineos": 0, "moffat": 0}
    for row in plan:
        if row.family == "gtp":
            preds = _forecast_gtp(data, row.code, row.area or "", scenarios, t0, horizon_days)
            mrid = f"gtp-{stamp}"
        elif row.family == "pirineos":
            preds = _forecast_pirineos(data, row.code, scenarios, t0, horizon_days)
            mrid = f"pirineos-{stamp}"
        elif row.family == "moffat":  # demand-like on German temperature (legacy quirk)
            drivers = {s: build_scenario_driver(data, "DE", s, t0) for s in scenarios}
            preds = predict_scenarios(
                get("lightgbm")(), data.read_target(row.code), drivers, t0, horizon_days
            )
            mrid = f"moffat-{stamp}"
        else:
            continue
        counts[row.family] += 1
        rows += _emit(preds, row.code, mrid, t0, horizon_days, stamp)
    log.info("covariate forecasts: %s x %d scenarios", counts, len(scenarios))
    return rows
