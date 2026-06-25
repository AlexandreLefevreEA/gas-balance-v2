# ml/ — the data-science core (`gasbalance_ml`)

The heart of the project. Reads clean series from Postgres, builds features, fits and
backtests models from a **registry**, tracks experiments in **MLflow**, and writes
forecasts back to Postgres. Make this as good as possible — and easy to experiment on.

## Layout

- `models/` — the base `Model` interface (`fit(series, covariates)`, `predict(n, covariates)`)
  + implementations + a **registry** so models are selected by config, not hardcoded in a loop.
- `features/` — covariates / feature engineering (temps→HDD/CDD, prices, availability, …).
- `pipelines/` — fit / predict / backtest / scenario orchestration.
- `evaluation/` — backtesting, metrics, model comparison.
- `tuning/` — Optuna studies (the SQLite db is git-ignored).
- `experiments/` — experiment configs (YAML) + the MLflow tracking dir.
- `notebooks/` — numbered exploration notebooks (kept out of the import path).

## How experimentation works

1. Define a model implementing the `Model` interface; register it (`/add-model`).
2. Write an experiment config in `experiments/`; runs log params/metrics/artifacts to MLflow.
3. Backtest in `evaluation/`; compare against the registry's current best before promoting.

## Rules

- Models are config-selected and comparable; no model is wired into a run loop.
- Keep the legacy `fit`/`predict` abstraction (it's good) — add the registry around it.
- Reuse `core/` for config/db. Heavy deps (torch, prophet, …) belong to this package only.

> Scaffold: subfolders are empty pending implementation.
