"""`ml` CLI — development (`backtest`/`tune`) and the running layer (`select`/`forecast`).

  backtest: rolling-origin evaluation (mode=actual perfect foresight by default, or
            vintage audit) -> prints the error surface + baseline skill.
  tune:     Optuna HPO over the same walk-forward -> prints best hyperparameters.
  select:   assign the best model to each demand series (data-driven) -> writes the
            series_models registry. The periodic, offline step.
  forecast: run every registry series under every weather scenario at an origin and write
            the forecasts to Postgres. The operational step.

DE LDZ example: --target CE.54 --actual-temp KP.TEMP.DE  (vintage audit:
--mode vintage --vintage-temp KP.TEMPFC.DE.EC_46).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from gasbalance_ml.data import PostgresData
from gasbalance_ml.pipelines import balance, custom
from gasbalance_ml.pipelines.forecast import generate_forecasts, generate_supply_forecasts
from gasbalance_ml.pipelines.power import generate_covariate_forecasts
from gasbalance_ml.pipelines.run import Config, run_backtest, run_tune
from gasbalance_ml.pipelines.select import select_models
from gasbalance_ml.plan import check_covariates
from gasbalance_ml.publish import publish_forecasts
from gasbalance_ml.registry import DEFAULT_PATH, load_registry, save_registry

log = logging.getLogger("gasbalance_ml")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--target", required=True, help="target series code, e.g. CE.54 (DE LDZ)")
    p.add_argument("--horizon", type=int, default=720, help="forecast horizon in days")
    p.add_argument("--model", default="lightgbm", help="registered model name")
    p.add_argument("--mode", default="actual", choices=["actual", "vintage"])
    p.add_argument("--actual-temp", help="covariate temp code (actual mode), e.g. KP.TEMP.DE")
    p.add_argument("--vintage-temp", help="forecast_covariate temp code (vintage mode)")
    p.add_argument("--window", default="expanding", choices=["expanding", "sliding"])
    p.add_argument("--sliding-years", type=int, default=5)
    p.add_argument("--from", dest="origin_from", required=True, help="first origin (YYYY-MM-DD)")
    p.add_argument("--to", dest="origin_to", required=True, help="last origin (YYYY-MM-DD)")
    p.add_argument("--step-days", type=int, default=30, help="spacing between origins")
    p.add_argument("--no-track", action="store_true", help="disable MLflow logging")


def _add_origins(p: argparse.ArgumentParser) -> None:
    p.add_argument("--from", dest="origin_from", required=True, help="first origin (YYYY-MM-DD)")
    p.add_argument("--to", dest="origin_to", required=True, help="last origin (YYYY-MM-DD)")
    p.add_argument("--step-days", type=int, default=30, help="spacing between origins")


def _run_backtest_or_tune(args: argparse.Namespace) -> int:
    cfg = Config(
        target_code=args.target,
        horizon_days=args.horizon,
        model=args.model,
        mode=args.mode,
        actual_temp_code=args.actual_temp,
        vintage_temp_code=args.vintage_temp,
        window=args.window,
        sliding_years=args.sliding_years,
        track=not args.no_track,
    )
    data = PostgresData()
    origins = list(pd.date_range(args.origin_from, args.origin_to, freq=f"{args.step_days}D"))

    if args.cmd == "tune":
        best = run_tune(data, cfg, origins, n_trials=args.n_trials)
        log.info("tune %s over %d origins -> best params:", cfg.target_code, len(origins))
        print(best)
        return 0

    out = run_backtest(data, cfg, origins)
    log.info(
        "backtest %s mode=%s: MAE=%.3f baseline=%.3f skill=%.3f over %d origins",
        cfg.target_code,
        cfg.mode,
        out["mae"],
        out["baseline_mae"],
        out["skill"],
        len(origins),
    )
    print(out["surface"].to_string(index=False))
    return 0


def _run_select(args: argparse.Namespace) -> int:
    data = PostgresData()
    universe = data.read_demand_universe()
    if args.only:
        only = set(args.only.split(","))
        universe = [(c, a) for c, a in universe if c in only]
    if not universe:
        log.warning("select: empty universe (check --only against the demand dictionary)")
        return 1
    origins = list(pd.date_range(args.origin_from, args.origin_to, freq=f"{args.step_days}D"))
    candidates = [m.strip() for m in args.candidates.split(",") if m.strip()]
    log.info("select: %d series x %s over %d origins", len(universe), candidates, len(origins))
    chosen = select_models(
        data,
        universe,
        origins,
        horizon_days=args.horizon,
        candidates=candidates,
        n_trials=args.n_trials,
        track=not args.no_track,
    )
    path = Path(args.registry)
    merged = load_registry(path)  # keep entries for series not selected this run
    merged.update(chosen)
    save_registry(merged, path)
    log.info("select: wrote %d entries to %s", len(chosen), path)
    for code, e in chosen.items():
        print(f"{code}\t{e['model']}\tskill={e['skill']:.3f}")
    return 0


def _run_forecast(args: argparse.Namespace) -> int:
    data = PostgresData()
    registry = load_registry(Path(args.registry))
    if args.only:
        only = set(args.only.split(","))
        registry = {c: e for c, e in registry.items() if c in only}
    if not registry:
        log.warning("forecast: empty registry (run `ml select` first, or check --only)")
        return 1
    made_on = dt.date.fromisoformat(args.made_on) if args.made_on else dt.date.today()
    scenarios = (
        [s.strip() for s in args.scenarios.split(",") if s.strip()]
        if args.scenarios
        else data.read_scenario_models()
    )
    if not scenarios:
        log.warning("forecast: no scenarios (no KP.TEMPLT.* series in the dictionary)")
        return 1
    rows = generate_forecasts(
        data,
        registry,
        scenarios,
        pd.Timestamp(made_on),
        horizon_days=args.horizon,
        made_on=made_on,
        window=args.window,
        sliding_years=args.sliding_years,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        log.warning("forecast: no rows produced")
        return 1
    log.info(
        "forecast made_on=%s: %d rows, %d series, %d scenarios",
        made_on,
        len(df),
        df["series_code"].nunique(),
        df["scenario"].nunique(),
    )
    if args.no_write:
        print(df.groupby("scenario")["value"].agg(["count", "mean"]).to_string())
        return 0
    res = publish_forecasts(rows, scenarios)
    log.info("forecast: wrote %d rows (forecast_run %d)", res["rows"], res["run_id"])
    return 0


def _last_actual_level(data: PostgresData) -> tuple[dt.date, float] | None:
    """Last realized EU storage level — the anchor the forecast level path cumsums from.
    Absent until the etl derived stage materializes EU.STORAGE.LEVEL -> start the path at 0."""
    try:
        actual = data.read_target(balance.LEVEL).dropna()
    except KeyError:
        return None
    if actual.empty:
        return None
    return (actual.index[-1].date(), float(actual.iloc[-1]))


def _run_orchestrate(args: argparse.Namespace) -> int:
    """The full balance run: base demand forecasts (expensive ML, once per weather year) ->
    close the EU balance per weather scenario -> layer each custom what-if (arithmetic, no
    refit) -> publish. Customs re-store only the series they touch; everything else falls
    back to its base weather scenario."""
    data = PostgresData()
    plan = data.read_forecast_plan()
    # Covariate-readiness report up front (Germany first): warn for any forecastable component
    # whose required driver is missing, before we spend time fitting.
    check_covariates(plan, data.present_codes)
    registry = load_registry(Path(args.registry))
    made_on = dt.date.fromisoformat(args.made_on) if args.made_on else dt.date.today()
    weather = data.read_scenario_models()
    if not weather:
        log.warning("run: no weather scenarios (no KP.TEMPLT.* series)")
        return 1

    # Every demand component must be forecast so EU.DEMAND is complete (the NaN rule), not just
    # the ones `select` has chosen. Use the selected model where present; fall back to the
    # seasonal_naive floor otherwise (a later `select` upgrades it). Supply / GTP / Pirineos /
    # Moffat are produced by their own passes below.
    effective = dict(registry)
    for row in plan:
        if row.family == "demand" and row.code not in effective:
            effective[row.code] = {
                "area": row.area,
                "model": "seasonal_naive",
                "params": {},
                "model_run_id": f"seasonal_naive-{made_on}",
            }
    if not effective:
        log.warning("run: no demand components found in the plan")
        return 1

    # 1) base demand-component forecasts (covariate-driven), per weather scenario —
    #    fit-once / predict-many, so the cost is series x weather, not x customs.
    component_rows = generate_forecasts(
        data,
        effective,
        weather,
        pd.Timestamp(made_on),
        horizon_days=args.horizon,
        made_on=made_on,
        window=args.window,
        sliding_years=args.sliding_years,
    )
    if not component_rows:
        log.warning("run: no component forecasts produced")
        return 1

    # 1b) static supply components (production/LNG/linepack/imbalance/...), weather-blind, and
    #     1c) covariate-driven components (GTP, Pirineos, Moffat), per weather scenario. Both may
    #     carry NaN cells where a source/covariate is absent — passed to close_balance so the gap
    #     surfaces, then filtered before publish (the forecast table is finite-only).
    supply_rows = generate_supply_forecasts(
        data, plan, weather, pd.Timestamp(made_on), horizon_days=args.horizon, made_on=made_on
    )
    covariate_rows = generate_covariate_forecasts(
        data, plan, weather, pd.Timestamp(made_on), horizon_days=args.horizon, made_on=made_on
    )
    extra_rows = supply_rows + covariate_rows
    components = pd.DataFrame(component_rows + extra_rows)
    catalog = data.read_catalog()
    last_level = _last_actual_level(data)

    all_rows: list[dict[str, object]] = list(component_rows)
    all_rows += [r for r in extra_rows if pd.notna(r["value"])]
    scenarios: set[str] = set(weather)

    # 2) close the balance for each base weather scenario
    all_rows += balance.close_balance(components, catalog, last_level, made_on=made_on)

    # 3) custom what-ifs: arithmetic overlay on the base components + re-close (never refits)
    for cust in data.read_customs():
        wanted = cust["weather_years"]
        years = weather if "*" in wanted else [w for w in wanted if w in weather]
        for w in years:
            base = components[components["scenario"] == w]
            if base.empty:
                continue
            adjusted, touched = custom.apply_adjustments(base, cust["adjustments"], catalog)
            combo = f"{cust['code']}@{w}"
            adjusted = adjusted.assign(scenario=combo)
            # store only the series the custom changed; untouched fall back to base weather
            all_rows += adjusted[adjusted["series_code"].isin(touched)].to_dict("records")
            all_rows += balance.close_balance(adjusted, catalog, last_level, made_on=made_on)
            scenarios.add(combo)

    if args.no_write:
        summary = pd.DataFrame(all_rows).groupby("scenario")["value"].agg(["count"])
        print(summary.to_string())
        return 0
    res = publish_forecasts(all_rows, scenarios)
    log.info(
        "run made_on=%s: wrote %d rows across %d scenarios (forecast_run %d)",
        made_on,
        res["rows"],
        len(scenarios),
        res["run_id"],
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    # Root at WARNING so third-party libs stay quiet; our own package logs at INFO.
    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("gasbalance_ml").setLevel(logging.INFO)
    parser = argparse.ArgumentParser(prog="ml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    _add_common(sub.add_parser("backtest", help="rolling-origin evaluation"))
    tune_p = sub.add_parser("tune", help="Optuna HPO over the walk-forward")
    _add_common(tune_p)
    tune_p.add_argument("--n-trials", type=int, default=30)

    select_p = sub.add_parser("select", help="assign the best model per series -> registry")
    _add_origins(select_p)
    select_p.add_argument("--horizon", type=int, default=720, help="forecast horizon in days")
    select_p.add_argument("--candidates", default="lightgbm", help="comma list of model names")
    select_p.add_argument("--n-trials", type=int, default=30)
    select_p.add_argument("--registry", default=str(DEFAULT_PATH), help="registry YAML path")
    select_p.add_argument("--only", help="comma list of series codes to (re)select")
    select_p.add_argument("--no-track", action="store_true", help="disable MLflow logging")

    fc_p = sub.add_parser("forecast", help="run all scenarios for all series -> forecast table")
    fc_p.add_argument("--made-on", help="run date / origin (YYYY-MM-DD; default today)")
    fc_p.add_argument("--horizon", type=int, default=720, help="forecast horizon in days")
    fc_p.add_argument("--scenarios", help="comma list of scenario codes (default: all discovered)")
    fc_p.add_argument("--registry", default=str(DEFAULT_PATH), help="registry YAML path")
    fc_p.add_argument("--only", help="comma list of series codes to forecast")
    fc_p.add_argument("--window", default="expanding", choices=["expanding", "sliding"])
    fc_p.add_argument("--sliding-years", type=int, default=5)
    fc_p.add_argument("--no-write", action="store_true", help="print a summary, write nothing")

    run_p = sub.add_parser(
        "run", help="forecast all weather years + close the balance + apply customs"
    )
    run_p.add_argument("--made-on", help="run date / origin (YYYY-MM-DD; default today)")
    run_p.add_argument("--horizon", type=int, default=720, help="forecast horizon in days")
    run_p.add_argument("--registry", default=str(DEFAULT_PATH), help="registry YAML path")
    run_p.add_argument("--window", default="expanding", choices=["expanding", "sliding"])
    run_p.add_argument("--sliding-years", type=int, default=5)
    run_p.add_argument("--no-write", action="store_true", help="print a summary, write nothing")

    args = parser.parse_args(argv)
    if args.cmd in ("backtest", "tune"):
        return _run_backtest_or_tune(args)
    if args.cmd == "select":
        return _run_select(args)
    if args.cmd == "run":
        return _run_orchestrate(args)
    return _run_forecast(args)


if __name__ == "__main__":
    raise SystemExit(main())
