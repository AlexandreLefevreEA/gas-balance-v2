# kpler_power_demand_long_term — Kpler long-term power demand (normals + weather years)

Hourly electricity **demand** (total system load, MW) per power zone, **forward-looking
climatology** used as a gas-for-power demand covariate — the long-term counterpart of the
day-ahead actuals in `kpler_power_demand`. Selected via Kpler's `baseWeatherModel` param:

- **MEAN** — the "normal" demand profile.
- **REF_YYYY** — the profile if that historical year's weather recurred ("weather years").
  We pull the **last 10 completed years**, recomputed each run (today `REF_2016 … REF_2025`).

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (shared with the other Kpler connectors; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Full, weekly.** Each run pulls the forward window `[today, today + 24 months]` for every model and upserts idempotently. |
| **Cadence** | Weekly (cron). |
| **Series** | 11 per balance area (MEAN + last-10 REF) — zones in `../../settings/kpler_power_demand_long_term.yaml`. Codes `KP.LOADLT.<zone>.<MODEL>`, `sub_group` = `demand`. |
| **Units** | MW. |
| **Stored in** | `covariate` (hourly `ts`), **not** `observation` (daily) — see ADR 0008. |

## Endpoint

`GET power/loads/forecasts/long-term?zones=<all>&baseWeatherModel=<M>&granularity=hourly&timezone=UTC&startDate=<today>&endDate=<+24mo>`
→ `{"data": [{zone, scenario:"no_scenario", startDate, value, provider, runDate}]}`.
The `zones[]` param returns **all areas in one request**, so a run is one request per
model (11 total), **fanned out concurrently** (bounded by `_CONCURRENCY`). The response
does **not** echo `baseWeatherModel`, so the connector tags each request's rows with the
model it asked for.

Two gotchas vs the short-term `/power/loads/forecasts` sibling (verified live): this
long-term endpoint takes **no `loadType` param** (it serves total demand, no
demand/residual split — passing `loadType` 422s), and `zones` is the **country-code**
enum, so Germany is `DE`, not the `DE-LU` bidding zone (passing `DE-LU` 422s).

## Why no runDate (run-date dependence)

Unlike the long-term temperatures (normals stable to ≤~0.005 °C across run dates), the
long-term **demand** profile is **not** run-date-independent: probing `MEAN` at the latest
run vs `runDate=2026-05-01` showed hours differing by **hundreds of MW** (~700 MW on a
~42 GW zone). So we omit `runDate` to take the **latest** run and full-refresh weekly into
the single-vintage `covariate` — no per-runDate vintaging (that is what
`kpler_power_demand_forecast` + `forecast_covariate` are for). Series dropped from the
dictionary as the 10-year window slides keep their stored rows (see `load/upsert.py`).

## Shape

`fetch(since)` fans out one request per model → tidy long frame `[zone, model, date(ts),
value]`. `to_canonical(raw)` merges with the dictionary on `(zone, model)` to attach
`series_id`/`area`/`sub_group`/…, producing canonical rows validated by
`../../validation/demand.py` (MW band). `load` routes them to `covariate` keyed by the
full hourly timestamp.

## Cadence (production)

No orchestrator (ADR 0001). Schedule with a weekly cron line on the host:

```cron
0 6 * * 1 cd /path/to/gas-balance-v2 && uv run etl run kpler_power_demand_long_term >> /var/log/etl-kpler-demand-lt.log 2>&1
```

## Adding / changing areas

Add a `{area, zone}` line to `kpler_power_demand_long_term.yaml`; the 11 model series are
generated in `connector.series_dict()`. To change the horizon or how many weather years,
edit `_HORIZON_MONTHS` / `_N_REF_YEARS` in `connector.py`.

## Test

`etl/tests/test_kpler_power_demand_long_term.py` — fixture-based, no live network. Covers
the dynamic model list (MEAN + last-10 REF), the `(zone, model)→canonical` mapping, the
unknown zone/model drop, and the MW sanity band. (Retry/backoff is shared and tested in
`test_kpler_http.py`.)
