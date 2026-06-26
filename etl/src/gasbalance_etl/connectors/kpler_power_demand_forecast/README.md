# kpler_power_demand_forecast — Kpler power demand forecast

Hourly electricity demand (total system load, MW) **forecasts** per power zone — the forecast
counterpart of `kpler_power_demand`, used as a *predicted* exogenous **covariate** for
gas-for-power demand. Two 00z models per zone, codes `KP.LOADFC.<zone>.<MODEL>`, `sub_group` =
the Kpler `loadType` (`demand`, same as the actual `KP.LOAD.<zone>`).

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Models** | `EC_AIFS_ENS` (AI ensemble, ~15-day) and `EC_46` (46-day extended, ~1-day publish lag) — both 00z. |
| **Refresh** | **Self-managing & backfills** — each run fetches the desired keep-set of run dates not already stored (+ a 3-day refresh overlap for revisions / the late EC_46). |
| **Cadence** | Daily (cron). |
| **Series** | 2 per balance area (`area` ↔ Kpler `zone`, identity map) in `../../settings/kpler_power_demand_forecast.yaml`; codes `KP.LOADFC.<zone>.<MODEL>`. |
| **Coverage** | EC_46 reaches back ~2024; EC_AIFS_ENS only the recent run dates. |
| **Units** | MW. |
| **Stored in** | `forecast_covariate` (vintage-keyed `(series_id, made_on, ts)`) — see ADR 0009. |

## Endpoint

`GET power/loads/forecasts?zones=<a>&zones=<b>&…&models=EC_AIFS_ENS&models=EC_46&run=00z&loadType=demand&runDate=<D>&granularity=hourly&timezone=UTC`
→ `{"data": [{startDate, zone, model, run, value, provider, runDate, updatedAt}]}`.

The load (demand) variant of the `power/loads/forecasts/temperature` endpoint that powers
`kpler_temps_forecast`, so that connector is the structural template. Differences from it /
from `kpler_generation_forecast`'s endpoint:

- **`zones` batches every zone in one request** (`zones=FR&zones=DE` → both) — unlike
  `kpler_generation_forecast` (one zone per request) — so a run is **one request per run date**.
  The zone enum accepts **plain country codes** (`DE`, not `DE-LU`).
- **`loadType`** ∈ {`demand`, `residual_demand`} is **required**; we pull total `demand`.
  `residual_demand` (load net of renewables) is a one-line add: loop `_LOAD_TYPE` and tag
  `sub_group`/code.
- **`models`** is **required** (a list; both returned at once, echoed per row).
- **`run=00z`** keeps the clean single daily run; omitting it returns all four sub-daily runs
  (00z/06z/12z/18z), 4× duplicated.

A run is `len(run_dates)` GETs (~67 on the first backfill: 15 daily + 52 Mondays; a handful
incrementally), **fanned out concurrently** (`httpx.AsyncClient` + `arequest`, bounded by
`_CONCURRENCY`) over the shared 429/5xx retry/backoff.

## Vintage & retention

A forecast is `(runDate, deliveryDate) → value`, so the same delivery hour recurs in every
daily run. Rows land in `forecast_covariate` keyed `(series_id, made_on, ts)` (`made_on` = the
run date) — every vintage kept, not overwritten. Storage is bounded by a **retention** rule
enforced in the `load` hook (and re-runnable via `etl prune kpler_power_demand_forecast`):
**keep all runs from the last 15 days + every Monday run for 1 year; delete the rest.** The
fetch keep-set is exactly this set, so we never pull a vintage we'd immediately prune. No
history floor is needed (EC_46 covers the whole trailing-year window).

## Gas day

We store the **raw hourly UTC** series. Gas-day (06:00→06:00 CET, DST-aware) aggregation is
applied **downstream in `ml/`**, not here.

## Shape

`fetch(since)` fans out one request per run date concurrently → a long frame
`[zone, model, date(ts), value, made_on]`. `to_canonical(raw)` merges the dictionary on
`(zone, model)`, drops unknown zones/models, carries `made_on`, and stamps the canonical
columns — validated by `forecast_covariate_demand_schema` (a generous MW band +
`unique(made_on, date, series_id)`). `load` routes the rows to `forecast_covariate` and prunes.

## Adding / changing areas

Add a line to `kpler_power_demand_forecast.yaml` with an `area` (balance area) and `zone`
(Kpler zone). Today every area maps 1:1 to the identically-named zone (plain country codes).

## Test

`etl/tests/test_kpler_power_demand_forecast.py` — fixture-based, no live network. Covers the
(zone, model) → canonical mapping carrying `made_on`, multiple vintages of one hour coexisting,
dropping unknown zones/models/nulls, the MW sanity band, the retention rule, and the fetch
keep-set.
