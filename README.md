# Gas Balance v2

Forecasting the EU natural-gas supply/demand balance.

A ground-up rebuild of the legacy pipeline (`legacy/`), which ran as a single ~1h
batch and produced Excel files. v2 splits the work into independent, tested,
version-controlled parts and serves results through an API + web app.

## Why v2

The legacy run was slow (~1h), monolithic (ingest + forecast + export in one
script), emitted files no one could query, had no data-validation layer, and made
model experimentation hard. v2 addresses each of those — see
[`docs/architecture.md`](docs/architecture.md).

## Layout

| Path | What |
|---|---|
| `etl/` | Ingest each data source independently → validate → load to Postgres |
| `ml/` | Data-science core: models, features, backtesting, experiments (MLflow) |
| `api/` | FastAPI service serving series & forecasts from Postgres |
| `web/` | React + Vite dashboard (consumes the API) |
| `core/` | Shared library: config, Postgres session, settings loader, logging, types |
| `infra/` | docker-compose dev stack + DB migrations |
| `docs/` | Architecture, data contracts, runbook, migration map, ADRs |
| `legacy/` | Frozen v1, local reference only (excluded from VCS) |

## Quickstart (once subsystems are implemented)

```bash
cp .env.example .env     # fill in values — never commit .env
make setup               # uv workspace + web deps
make dev                 # docker-compose: postgres + api + web
make test
```

## Status

**Scaffold.** Structure, docs and manifests are in place; subsystem source is
added incrementally. Every folder has a `CLAUDE.md` explaining how to work in it.

## Stack

Python 3.12 (uv workspace) · FastAPI · Pandera (data validation) ·
Darts / scikit-learn / Optuna (forecasting) · MLflow (experiments) · PostgreSQL ·
React + Vite · pytest + vitest.

## Decisions

Key choices — web = React+Vite, store = PostgreSQL, lightweight ETL CLI, lean DS
tooling, source-agnostic connectors — are recorded as ADRs in
[`docs/adr/`](docs/adr/).
