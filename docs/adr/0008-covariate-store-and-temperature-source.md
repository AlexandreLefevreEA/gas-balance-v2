# 0008. Sub-daily covariates in a `covariate` table; Kpler temperature as a D-1 actual proxy

- Status: Accepted
- Date: 2026-06-25

## Context

The first demand driver — actual temperature (Kpler) — is **hourly** and is a
**covariate**, not a gas-balance actual. Two facts collided with the scaffold:

1. `observation` is **daily** (PK `(series_id, obs_date::Date)`) and the loader truncates
   datetimes to the day; it physically cannot hold 24 hourly values per series/day. Its
   model docstring scopes it to "Actuals", and the `0001` migration explicitly noted
   *"Covariate … tables come in later migrations."*
2. Kpler exposes **no observed/reanalysis** temperature — every endpoint is an NWP
   forecast. The efficient `…/temperature/horizons` (D-0, whole-range in one request)
   only has data from **2025-10-31**, too short to train a daily demand model.

So: *where do hourly covariates live*, and *what is the actual-temperature source*?

Probed live against the v2 API: the base `…/forecasts/temperature` endpoint archives
past 00z runs back to **~spring 2018**; for historical runs the same-day (D-0) slice is
only ~2 h, but the **next-day (D-1) slice is a clean 24 h**. The `zones[]` param returns
all areas in one request. `…/long-term` offers 2011–2023 but it is climatology
simulation, not actuals.

## Decision

**Storage.** Add a `covariate` table — `(series_id, ts timestamptz)` PK, `value`,
`run_id`, `loaded_at`, finite-value check — mirroring `Observation` but timestamped, so
it holds sub-daily drivers. It is **additive**: `observation` and every existing reader
are untouched. A connector opts in by exposing a `load` hook; the CLI calls
`getattr(conn, "load", upsert_observations)`, so the default daily path is unchanged.
Covariates keep **raw hourly fidelity** at the storage layer; aggregation to daily / HDD
happens later in `ml/features`, never at ingest.

We chose this over (B) altering `observation` to be sub-daily — which breaks the PK that
all four subsystems and `transforms/derived` depend on — and over (C) collapsing temps to
a daily mean in `observation`, which discards the data and mislabels a driver as an
actual.

**Source.** `kpler_actual_temps` reads the base `…/forecasts/temperature` endpoint and
uses the **day-ahead (D-1) slice of the 00z EC_OP run** as the actual-temperature proxy —
the one definition consistent across the whole archive (~2018 → present). Fetch is
**incremental and self-managing**: it reads the last loaded `covariate` timestamp and
pulls only newer delivery days (one request per run-day, all zones at once); the first
run backfills from 2018. Per-zone coverage varies (Western-EU from ~2018, others later);
absent zones are skipped.

## Consequences

- Easy: covariates have a clean, single-grain home reused by every future driver (wind,
  solar, prices); the validation gate and idempotent-upsert pattern carry over; near-zero
  blast radius (one new table + a one-line CLI hook).
- The actual temperature is a **1-day-ahead forecast**, not an observation — good enough
  as a demand driver, but labelled as such. The same-day nowcast (more accurate, 2025-10+
  only) is deliberately not spliced in, to keep one consistent methodology.
- History stops at ~2018; matching the CE gas history (2014) needs external ERA5
  reanalysis — a separate connector, deferred until needed.
- Give up: the first run is a ~3 000-request backfill (a few minutes). Routine runs are a
  handful of requests.

## Trigger to revisit

- **ERA5 / observed source**: when pre-2018 history or true observations are needed.
- **Hybrid D-0 + D-1**: if recent nowcast accuracy matters more than methodology
  continuity.
- **Promote covariates to a covariate-aware API/feature path**: when `ml/` or `api/`
  starts consuming them (today only ETL writes them).
