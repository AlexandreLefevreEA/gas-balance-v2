---
description: Scaffold a new forecasting model in the ml core
argument-hint: <model-name>
---

Add a new model `$ARGUMENTS` to `ml/src/gasbalance_ml/models/`.

Follow `ml/CLAUDE.md`:

1. Implement the base `Model` interface — `fit(series, covariates)` and `predict(n, covariates)`.
2. Register it in the model registry so it can be selected by config (not hardcoded in a run loop).
3. Add an experiment config under `ml/experiments/` and log runs to MLflow.
4. Add a backtest in `ml/src/gasbalance_ml/evaluation/` and a unit test in `ml/tests/`.
5. Compare against the current best in the registry before promoting it.
