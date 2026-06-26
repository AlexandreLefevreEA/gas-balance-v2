# kpler_generation_forecast

The **forecast** counterpart of `kpler_generation_actual`, kept per vintage — generation
forecast as a forecast covariate for gas-for-power demand.

| | |
|---|---|
| **Endpoint** | `GET /power/generations/forecasts` |
| **Auth** | HTTP Basic, `KPLER_API_KEY_V2` (shared with every Kpler connector) |
| **Fuels** | solar, wind (onshore + offshore summed → WIND), run-of-river, gas — same as the actual |
| **Models** | `EC_AIFS_ENS` (AI ensemble, ~15-day) + `EC_46` (46-day extended), 00z runs |
| **Grain** | hourly, MW |
| **Series** | one per (zone × fuel × model): `KP.GENFC.<FUEL>.<zone>.<MODEL>`; `sub_group` = fuel |
| **Store** | `forecast_covariate`, PK `(series_id, made_on, ts)` — see ADR 0009 |

## The vintage dimension

A forecast is `(runDate, deliveryDate) → value`: the same delivery hour is forecast by every
daily run. Those vintages land in **`forecast_covariate`** (which adds `made_on`, the run date,
to the key), validated by `forecast_covariate_generation_schema`
(`unique(made_on, date, series_id)`) — exactly like `kpler_temps_forecast`.

## Request grain (API constraint)

Unlike the temperature-forecast endpoint, this one takes **one `zone` and one `fuelType` per
request** (only `models` is a true list — both models come back in one call) and needs
**`run=00z`** (without it the response carries all four sub-daily runs, 4× duplicated). The
response echoes `model` and `runDate` but **not** `fuelType`, so `fetch()` tags each row with
the fuel it requested. Net: one request per (run date × zone × fuel) — these are fanned out
concurrently (bounded by `_CONCURRENCY`) over the shared 429/5xx retry, so the backfill isn't
thousands of serial round-trips.

## Refresh & backfill

Self-managing — `fetch()` ignores the framework `since`. Each run computes the desired
keep-set of run dates (= the retention set, clamped to `_HISTORY_START`), subtracts the
`made_on`s already stored, and fetches every missing vintage plus a `_REFRESH_DAYS` recent
overlap (revisions, and the EC_46 run which publishes ~1 day late).

`_HISTORY_START` (≈ Jan 2026) floors the keep-set: generation-forecast vintages don't go back
further, so without the floor the trailing-year Mondays that predate the data would return
empty, store nothing, and be re-requested every run. Widen it if Kpler backfills.

## Retention

Enforced in the `load` hook after every load (same transaction), and re-runnable standalone
via `etl prune kpler_generation_forecast`:

- keep **all** runs from the **last 15 days**,
- keep **every Monday** run for **1 year**,
- delete everything else.

The rule lives in the pure `_vintages_to_delete(made_ons, today)` (unit-tested); `prune()`
wraps it in a single `DELETE`. Tune via `_KEEP_DAILY_DAYS` / `_KEEP_MONDAY_DAYS`.
