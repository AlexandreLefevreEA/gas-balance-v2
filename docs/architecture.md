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
  - **kpler_long_term_temperatures** (Kpler) — hourly forward-looking temperature
    **climatology** per power zone, a demand **covariate**, from
    `…/temperature/long-term`. Two flavours via `baseWeatherModel`: **MEAN** (the normal)
    and **REF_YYYY** weather years (last 10 completed years, recomputed each run) — so 11
    series per area, codes `KP.TEMPLT.<zone>.<MODEL>`. HTTP Basic Auth (shared Kpler key),
    JSON; **full refresh weekly** of the forward window `[today, +24 months]` (profiles are
    run-date-independent, so `runDate` is omitted). Hourly → lands in the `covariate` table
    via the `load` hook. Zones in `settings/kpler_long_term_temperatures.yaml`. (ADR 0008.)
  - **kpler_temps_forecast** (Kpler) — hourly temperature **forecasts** per power zone, the
    first **forecast covariate**, from the base `…/forecasts/temperature` endpoint. Two 00z
    models — **EC_AIFS_ENS** (AI ensemble, ~15-day) and **EC_46** (46-day extended) —
    ensemble-mean value; 2 series per area, codes `KP.TEMPFC.<zone>.<MODEL>`. A forecast has
    a **vintage** dimension (`runDate`) the actuals don't, so it lands in a new
    **`forecast_covariate`** table keyed `(series_id, made_on, ts)`, validated by
    `forecast_covariate_temperature_schema` (`unique(made_on, date, series_id)`) — see ADR
    0009. **Self-managing & backfills**: each run fetches the desired keep-set of run dates
    not already stored (+ a recent refresh overlap). A **retention** rule (keep last 15 days
    of runs + every Monday for 1 year) runs in the `load` hook and via `etl prune
    kpler_temps_forecast`. Zones in `settings/kpler_temps_forecast.yaml`. (ADR 0009.)
  - **kpler_generation_actual** (Kpler) — hourly actual power generation (MW) by fuel per
    power zone, an exogenous **covariate** for gas-for-power demand, from
    `…/power/generations/fuel-types`. Four fuels — **solar, wind, run-of-river, gas** (Kpler's
    `wind onshore` + `wind offshore` are summed into one WIND series); 4 series per area,
    codes `KP.GEN.{SOLAR,WIND,ROR,GAS}.<zone>`. HTTP Basic Auth (shared Kpler key), JSON;
    **incremental** (self-managed from the last loaded timestamp; first run backfills from
    2015 in 90-day chunks, all zones per request). Hourly → lands in the `covariate` table via
    the `load` hook. We store raw hourly UTC; the EU **gas-day (06:00 CET)** aggregation is
    applied downstream in `ml/` (Kpler's own `daily` is calendar-day, the wrong boundary).
    Zones in `settings/kpler_generation_actual.yaml`. (ADR 0008.)
  - **kpler_generation_forecast** (Kpler) — the **forecast** counterpart of
    `kpler_generation_actual`, kept per vintage, from `…/power/generations/forecasts`. Same
    four fuels (**solar, wind, run-of-river, gas**; `wind onshore` + `wind offshore` folded)
    and the two 00z models of `kpler_temps_forecast` — **EC_AIFS_ENS** (~15-day) and **EC_46**
    (46-day, ~1-day publish lag); one series per (zone × fuel × model), codes
    `KP.GENFC.<FUEL>.<zone>.<MODEL>`, `sub_group` = fuel. Like `kpler_temps_forecast` it has a
    **vintage** dimension, so it lands in **`forecast_covariate`** keyed `(series_id, made_on,
    ts)`, validated by `forecast_covariate_generation_schema` (`unique(made_on, date,
    series_id)`). **Self-managing & backfills** the keep-set (floored at `_HISTORY_START`
    ≈ Jan 2026, where the data begins) + a refresh overlap; same **retention** (last 15 days +
    every Monday for a year) via the `load` hook and `etl prune kpler_generation_forecast`.
    The endpoint takes **one zone + one fuelType per request** (`run=00z`; only `models` is a
    list, and `zones` is an ENTSO-E **bidding-zone** enum — Germany is `DE-LU`, not `DE`), so
    fetch issues one request per (run date × zone × fuel), **fanned out concurrently** (bounded
    by `_CONCURRENCY`). Zones in `settings/kpler_generation_forecast.yaml`. (ADR 0009.)
  - **kpler_generation_long_term** (Kpler) — hourly forward-looking **renewable** generation
    (MW) **climatology** per power zone, a covariate, from `…/power/generations/forecasts/long-term`
    — the generation analogue of `kpler_long_term_temperatures`. Two flavours via
    `baseWeatherModel`: **MEAN** (the normal) and **REF_YYYY** weather years (last 10 completed
    years, recomputed each run). The endpoint's `fuelType` enum is exactly the three renewables —
    **solar, wind, run-of-river** (no gas; **wind is one fuel**, no onshore/offshore split) — one
    per request, while `zones[]` batches every area, so a run is `fuels × models` (= 3 × 11 = 33)
    requests. 33 series per area, codes `KP.GENLT.<FUEL>.<zone>.<MODEL>`, `sub_group` = fuel
    (`zones` is the ENTSO-E **bidding-zone** enum — Germany is `DE-LU`). HTTP Basic Auth (shared
    Kpler key), JSON; **full refresh weekly** of the forward window `[today, +24 months]`. Unlike
    the long-term temperatures this profile is **not** run-date-independent (it shifts by hundreds
    of MW across run dates), so we deliberately omit `runDate` to take the **latest** run and
    overwrite the **single-vintage `covariate`** in place — we do not vintage it (that's
    `kpler_generation_forecast`). Validated by the shared `generation_schema` (MW band). Zones in
    `settings/kpler_generation_long_term.yaml`. (ADR 0008.)
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
