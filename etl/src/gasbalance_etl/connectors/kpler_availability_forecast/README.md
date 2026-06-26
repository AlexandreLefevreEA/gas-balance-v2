# kpler_availability_forecast — Kpler plant-availability vintages

The **forward** counterpart of `kpler_availability`: the `asOf` snapshots of the availability
outlook (coal, gas, lignite, nuclear, MW per country), captured *as known at each `asOf`*. Each
snapshot carries the planned-outage view (delivery dates from `asOf` forward), kept per vintage so
the model can see how the outlook evolved. Stores the `central` estimate.

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Self-managing & backfilling** — each run fetches every keep-set `asOf` not yet stored (+ a 3-day overlap), fanned out **async** (bounded by `_CONCURRENCY`). One request per `asOf` over a `_HORIZON_DAYS` (365) forward window. |
| **Cadence** | Daily (cron). |
| **Series** | 4 per balance area in `../../settings/kpler_availability_forecast.yaml`; codes `KP.AVAILFC.{COAL,GAS,LIGNITE,NUCLEAR}.<zone>`, `sub_group` = fuel. **No model dimension.** |
| **Vintage** | `made_on` = the `asOf` snapshot date. Keyed `(series_id, made_on, ts)` in `forecast_covariate`. |
| **Retention** | Keep all snapshots of the last **15 days** + every **Monday** for **1 year**; delete the rest. Enforced after each load and via `etl prune kpler_availability_forecast` (shared rule, ADR 0009). |
| **Units** | MW. |
| **Stored in** | `forecast_covariate` — see ADR 0009. |

## Endpoint

`GET power/outages/availability/fuel-types?zones=<all>&fuelTypes=<4>&granularity=daily&timezone=UTC&startDate=<asOf>&endDate=<asOf+horizon>&asOf=<asOf>`
→ `{"data": [{asOf, provider, zone, fuelType, startDate, low, central, high}]}`. **`asOf` is the
vintage param** — passing a past date returns the outlook as it stood then (probed back to
2024-01, deeper than the trailing-year keep-set, so no history floor is needed). `zones` (plain
**country codes** — `DE`, not `DE-LU`) and `fuelTypes` both batch into one request, so a run is
one request per `asOf`.

## Shape

`fetch(since)` computes the keep-set of `asOf` dates (shared `desired_run_dates`), subtracts what
`forecast_covariate` already holds (`loaded_run_dates`), and async-fetches the rest (+ overlap) →
`[zone, fuelType, date(ts), value, made_on]` (`value` = `central`, dropping nulls; `made_on` = the
requested `asOf`). `to_canonical(raw)` maps each `fuelType` to one of our four codes, drops
unmapped fuels / unknown zones / nulls, and merges on `(zone, fuel)` — fuels are 1:1, so
`(zone, fuel, day, vintage)` is unique. Canonical rows (carrying `made_on`) are validated by
`forecast_covariate_availability_schema` (the MW band + the vintage key). `load` upserts into
`forecast_covariate` and then prunes to the retention window in the same transaction.

## Test

`etl/tests/test_kpler_availability_forecast.py` — fixture-based, no live network. Covers the
(zone, fuel) → canonical mapping carrying `made_on`, dropping unmapped fuels / unknown zones, and
that the connector wires the shared retention helpers (`desired_run_dates` / `vintages_to_delete`
keep the last 15 days + Mondays and drop the rest).
