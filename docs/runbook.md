# Runbook

> Scaffold stage: commands below describe the intended operations; they go live as
> subsystems are implemented.

## Local development

```bash
cp .env.example .env          # fill in DB + source credentials
make setup                    # uv workspace + web deps
make dev                      # docker-compose: postgres + api + web
```

API at `http://localhost:8000`, web at the Vite dev URL. Stop with Ctrl-C.

## Running the pipeline

```bash
make run-etl                  # all connectors → validate → load to Postgres
uv run etl run <source>       # a single source in isolation
# then forecasting:
uv run ml forecast            # fit/backtest/forecast → write forecasts to Postgres
```

ETL and forecasting are independent — run, retry, or backfill either alone.

## Scheduling (production)

Cron/CI triggers `etl run all` on the source's natural cadence, then `ml forecast`.
No orchestrator yet (ADR 0001); revisit if backfills or cross-job lineage get painful.

## Failure handling

- **A connector fails validation** → that source's load is blocked; others proceed.
  Inspect the logged offending rows, fix the mapping or schema, re-run that source.
- **A connector fails to fetch** → it's isolated; re-run just that source once the
  upstream recovers. Incremental fetch means a re-run only pulls the missing delta.
- **A model errors** → forecasting falls back per `ml/` policy; the registry's last
  good forecast remains served by the API.

## Tests & checks

```bash
make test                     # pytest + vitest
make lint                     # ruff + mypy + eslint + tsc
uv run pre-commit run -a      # format, secret scan, yaml checks
```
