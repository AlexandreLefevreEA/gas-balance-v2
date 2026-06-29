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
from gasbalance_ml.pipelines.forecast import generate_forecasts
from gasbalance_ml.pipelines.run import Config, run_backtest, run_tune
from gasbalance_ml.pipelines.select import select_models
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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
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

    args = parser.parse_args(argv)
    if args.cmd in ("backtest", "tune"):
        return _run_backtest_or_tune(args)
    if args.cmd == "select":
        return _run_select(args)
    return _run_forecast(args)


if __name__ == "__main__":
    raise SystemExit(main())
