# kpler_temps_forecast

The first **forecast covariate**: Kpler temperature forecasts, kept per vintage.

| | |
|---|---|
| **Endpoint** | `GET /power/loads/forecasts/temperature` (base forecast endpoint) |
| **Auth** | HTTP Basic, `KPLER_API_KEY_V2` (shared with `kpler_actual_temps`) |
| **Models** | `EC_AIFS_ENS` (AI/AIFS ensemble, ~15-day horizon) + `EC_46` (46-day extended), 00z runs |
| **Value** | ensemble **mean**, one per (zone, hour); response echoes `model` + `runDate` |
| **Grain** | hourly, °C, population-weighted 2 m temperature |
| **Series** | one per (area × model): `KP.TEMPFC.<zone>.<MODEL>`; `sub_group` = model |
| **Store** | `forecast_covariate`, PK `(series_id, made_on, ts)` — see ADR 0009 |

## The vintage dimension

A forecast is `(runDate, deliveryDate) → value`: the same delivery hour is forecast by
every daily run. That breaks the single-vintage `covariate` table (PK `(series_id, ts)`
overwrites) and the canonical `unique(date, series_id)` rule. So forecasts land in the
**`forecast_covariate`** table, which adds `made_on` (the run date) to the key, validated
by `forecast_covariate_temperature_schema` (`unique(made_on, date, series_id)`).

## Refresh & backfill

Self-managing — `fetch()` ignores the framework `since`. Each run:

1. computes the **desired keep-set** of run dates (= the retention set, below),
2. subtracts the `made_on`s already stored,
3. fetches **every missing vintage** (first run loads the whole set; a daily cron loads
   today's run; a run after missed crons backfills the gap), plus a 3-day recent overlap
   re-pulled to pick up revised runs.

One request per run date returns both models for all zones (`zones[]`, `models[]`).
`made_on` is taken from the response `runDate`, so re-fetching a stored vintage is a no-op.

## Retention

Enforced in the `load` hook after every load (same transaction), and re-runnable
standalone via `etl prune kpler_temps_forecast`:

- keep **all** runs from the **last 15 days**,
- keep **every Monday** run for **1 year**,
- delete everything else.

The rule lives in the pure `_vintages_to_delete(made_ons, today)` (unit-tested); `prune()`
wraps it in a single `DELETE`. Both models are daily with >2 years of history, so every
Monday in the trailing year exists for both. Tune the window via `_KEEP_DAILY_DAYS` /
`_KEEP_MONDAY_DAYS`.
