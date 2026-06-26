# 0009. Forecast covariates in a vintage-keyed `forecast_covariate` table; Kpler temperature forecast source

- Status: Accepted
- Date: 2026-06-25

## Context

The first **forecast** input — Kpler temperature forecasts (`kpler_temps_forecast`) — is a
demand covariate like the actuals and climatology of ADR 0008, but it carries a dimension
they don't: a **vintage**. A forecast is `(runDate, deliveryDate) → value`; the same
delivery hour is forecast afresh by every daily run, and we want to keep those vintages
(to train/evaluate on the information actually available at each point in time).

That collided with the storage from ADR 0008:

1. `covariate` is keyed `(series_id, ts)` — a second vintage of the same hour **overwrites**
   the first. It physically cannot hold many runs of one delivery hour.
2. The canonical Pandera schema enforces `unique(date, series_id)` — multiple vintages of
   one delivery hour are rejected as duplicates.
3. The existing `forecast` table is **not** a fit: it is daily (`target_date::Date`) and
   its PK/FKs are ML-output-shaped (`scenario` FK + MLflow `model_run_id` + `forecast_run`).
   Putting an hourly exogenous weather input there mislabels it and muddies the
   ml-writes / api-reads contract.

Probed live against the v2 API (read-only): the base `…/forecasts/temperature` endpoint
serves both **`EC_AIFS_ENS`** (AI/AIFS ensemble, ~15-day horizon) and **`EC_46`** (46-day
extended) at `run=00z`; **both are daily** (every consecutive calendar day returns its own
`runDate`, with daily history back to ~mid-2024 — well over a year). Each row is the
**ensemble mean** (one value per zone/hour) and echoes `model` + `runDate`. One request
returns both models for all zones.

## Decision

**Storage.** Add a `forecast_covariate` table — `(series_id, made_on date, ts timestamptz)`
PK, `value`, `run_id`, `loaded_at`, finite check, plus `ix_forecast_covariate_latest
(series_id, ts, made_on)`. It mirrors `covariate` (ADR 0008) with `made_on` (the run date)
added to the key, so every vintage is retained. It is **additive**: `observation`,
`covariate`, `forecast` and every reader are untouched. A new
`forecast_covariate_temperature_schema` reuses the temperature `[-60, 60] °C` guard but
swaps the uniqueness to `(made_on, date, series_id)`. A new `upsert_forecast_covariates`
sink is reached via the connector's existing `load` hook.

We chose this over (B) adding `made_on` to `covariate`'s PK — which forces a vintage
dimension on single-run drivers (actuals, climatology) that don't have one — and over (C)
reusing the `forecast` table — wrong grain (daily) and wrong semantics (ML outputs).

**Source.** `kpler_temps_forecast` keeps the **full forward horizon** of the 00z
`EC_AIFS_ENS` and `EC_46` runs (ensemble mean), one series per (area × model)
`KP.TEMPFC.<zone>.<MODEL>`, `made_on` taken from the response `runDate`. Fetch is
**self-managing and backfills**: each run computes the desired keep-set of run dates (= the
retention set), subtracts the `made_on`s already stored, and fetches every missing vintage
plus a small recent overlap (revised runs). Re-fetching a stored vintage is an idempotent
no-op.

**Retention.** Multi-vintage storage is bounded by a policy: keep **all** runs from the
last **15 days**, plus **every Monday** run for **1 year**; delete the rest. The rule is a
pure, unit-tested function (`_vintages_to_delete`); `prune()` wraps it in one `DELETE`. It
is enforced in the `load` hook (same transaction, every run) and re-runnable standalone via
`etl prune kpler_temps_forecast`. App-level, not a DB trigger/pg_cron — version-controlled
and testable, consistent with the rest of the repo.

## Consequences

- Easy: a clean, vintage-aware home that future forecast covariates (other NWP models,
  wind/solar forecasts) reuse; the validation gate, idempotent-upsert and `load`-hook
  patterns carry over; near-zero blast radius (one new table + one schema + one sink).
- Forecasts are stored as forecasts (every vintage), not collapsed to a latest-only series,
  so backtests can use the information available at each `made_on`.
- The retention rule caps volume to ~15 daily + ~52 weekly vintages per series. Both models
  are daily with >2 years of history, so every Monday in the trailing year exists for both.
- Give up: the first run backfills the whole keep-set (~67 run dates, one request each);
  routine runs are a handful of requests. Aggregation to daily / HDD stays in `ml/features`,
  never at ingest (as with all covariates).

## Trigger to revisit

- **Ensemble spread**: if ML needs forecast uncertainty, store quantiles/members (more
  series) instead of the mean only — the table already keys per series, so additive.
- **More forecast models / sources**: each is a new connector writing the same table.
- **Partitioning**: range-partition `forecast_covariate` on `made_on` if row counts warrant
  it (the `0001` migration already flagged this for vintage tables).
- **Promote to a forecast-covariate-aware feature/API path**: when `ml/` or `api/` starts
  consuming vintages (today only ETL writes them).
