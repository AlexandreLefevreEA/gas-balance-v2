# gasbalance-ml

The forecasting core. Models behind one `fit`/`predict` interface in a registry,
feature engineering, backtesting, Optuna tuning, and MLflow-tracked experiments.
Reads clean series from Postgres and writes forecasts back.

Add a model with `/add-model`. See [`CLAUDE.md`](CLAUDE.md).
