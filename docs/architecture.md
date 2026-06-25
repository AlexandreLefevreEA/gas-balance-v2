# Architecture

## Purpose

Forecast the EU natural-gas supply/demand balance: per-country/per-zone demand,
supply, flows and storage, projected ~2 years forward, in multiple scenarios — and
make those numbers **queryable, trustworthy, and cheap to re-experiment on**.

## The four parts (separation of concerns)

```
                    ┌──────────────────────────────────────────────┐
   data sources ───▶│  etl/   fetch → transform → VALIDATE → load   │───▶ Postgres
   (per connector)  └──────────────────────────────────────────────┘        │
                                                                              │
                    ┌──────────────────────────────────────────────┐         │
                    │  ml/    features ← read                       │◀────────┤
                    │         fit / backtest / forecast → write     │────────▶│ (forecasts)
                    └──────────────────────────────────────────────┘         │
                                                                              │
                    ┌─────────────────┐         ┌────────────────┐           │
        browser ───▶│  web/ React+Vite│ ──────▶ │  api/ FastAPI  │◀──────────┘
                    └─────────────────┘  HTTP   └────────────────┘   reads
```

- **etl/** — one independent connector per data source. Each fetches its raw data,
  maps it to the canonical series schema, is **validated** (Pandera) before it can
  land, and loads into Postgres. Sources can be added/swapped/run in isolation.

  **Sources**
  - **ce** (Commodity Essentials) — European gas fundamentals (flows, storage, demand,
    supply). HTTP Basic Auth, CSV `eugasseries` endpoint; **full refresh** hourly
    (re-fetch history since 2014, idempotent upsert); curated series dictionary in
    `etl/src/gasbalance_etl/settings/ce.yaml`. The first connector — it also bootstraps
    the shared CLI (`etl run <source>`), the load/upsert step, and the canonical Pandera
    schema, all kept source-agnostic. (ADR 0003.)
  - **kpler_actual_temps** (Kpler) — hourly actual temperature (°C) per power zone, a
    demand **covariate**. Kpler has no observed product, so "actual" = the day-ahead
    (D-1) slice of each archived 00z EC_OP run (consistent back to ~2018). HTTP Basic
    Auth, JSON; **incremental** (self-managed from the last loaded timestamp; first run
    backfills). Hourly, so it lands in the `covariate` table — keyed by timestamp,
    separate from the daily `observation` actuals — via the connector's `load` hook.
    Areas map 1:1 to Kpler zones in `settings/kpler_actual_temps.yaml`. (ADR 0008.)
- **ml/** — the data-science core. Reads clean series from Postgres, builds features
  (covariates), fits/backtests models from a registry, tracks experiments in MLflow,
  and writes forecasts back to Postgres. Models are config-selected, not hardcoded.
- **api/** — FastAPI. Serves series and forecasts from Postgres. No business logic
  beyond shaping/serving; it never re-runs the pipeline.
- **web/** — React + Vite dashboard. Talks only to the API.
- **core/** — shared config, Postgres session, settings loader, logging, types.
  Everything else imports from here instead of re-implementing.

## Why Postgres in the middle

The store is the contract between producers (etl, ml) and consumers (api). Producers
write; the API reads. The web app never sees the pipeline, and the pipeline never
serves HTTP. Reuses the existing v1 power DB — no new infra (ADR 0002).

## Trusted data

"100% trust" is a layer, not a hope. Every connector's output passes a Pandera
schema (columns, dtypes, ranges, nullability) and freshness/duplicate checks
**before** load. Bad data fails loudly and is never written. Details and the list of
checks: [`data-contracts.md`](data-contracts.md).

## Performance — killing the ~1h run

The legacy run was slow because it re-fetched everything, retrained every model, and
looped series sequentially. v2 design targets (implementation goals, not yet built):

- **Incremental fetch** — connectors pull only the delta since last load.
- **Persisted models** — fit once, reuse; retrain on a schedule or when drift is detected.
- **Serve, don't recompute** — the API reads stored forecasts; it never triggers a run.
- **Parallelism at the series level**, not just per-scenario.
- **Decoupled stages** — ETL and forecasting run (and scale) independently via the CLI.

## Orchestration

Lightweight by choice (ADR 0001 / decisions): per-source connectors behind a CLI
(`etl run <source>`), scheduled by cron/CI. No Dagster/Prefect until lineage or
backfills actually demand it.

## Experimentation

Models live in a registry; experiments are config-driven and tracked in MLflow
(local file backend to start). Adding/comparing a model is `/add-model` + a backtest,
not a surgery on the run loop.
