# kpler_generation_long_term — Kpler long-term renewable generation (normals + weather years)

Hourly **renewable** generation (MW) per power zone, **forward-looking climatology** used as a
covariate — the generation analogue of `kpler_long_term_temperatures` and the climatology
counterpart of `kpler_generation_actual`. Selected via Kpler's `baseWeatherModel` param:

- **MEAN** — the "normal" generation profile.
- **REF_YYYY** — the profile if that historical year's weather recurred ("weather years").
  We pull the **last 10 completed years**, recomputed each run (today `REF_2016 … REF_2025`).

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (shared with the other `kpler_*` connectors; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Full, weekly.** Each run pulls the forward window `[today, today + 24 months]` for every (fuel, model) and upserts idempotently. |
| **Cadence** | Weekly (cron). |
| **Series** | 33 per balance area (3 renewable fuels × 11 models: MEAN + last-10 REF) — zones in `../../settings/kpler_generation_long_term.yaml`. Codes `KP.GENLT.<FUEL>.<zone>.<MODEL>`, `sub_group` = fuel. |
| **Units** | MW. |
| **Stored in** | `covariate` (single-vintage, hourly `ts`), **not** `forecast_covariate` — see ADR 0008. |

## Endpoint

`GET power/generations/forecasts/long-term?zones=<all>&fuelType=<F>&baseWeatherModel=<M>&granularity=hourly&timezone=UTC&startDate=<today>&endDate=<+24mo>`
→ `{"data": [{zone, scenario:"no_scenario", runDate, startDate, value, provider}]}`.

Probed quirks (live, with `.env` creds):

- **`fuelType` is required + singular**, and its enum is exactly the three renewables —
  `solar`, `wind`, `hydro run-of-river and poundage` (**no gas**). **`wind` is one fuel** here
  (no onshore/offshore split → no folding), unlike the actual/forecast endpoints.
- **`zones[]` batches all areas in one request**, so a run is `fuels × models` (= 3 × 11 = 33)
  requests, **not** one-per-zone like the short-term `…/forecasts` endpoint.
- `zones` is the ENTSO-E **bidding-zone** enum — Germany is **`DE-LU`** (plain `DE` 422s).
- The response **does not echo** `fuelType` or `baseWeatherModel`, so the connector tags each
  request's rows with the fuel code and model it asked for.
- **No `models` / `run` params** (those belong to the short-term forecast endpoint).
- **Forward-only**: a past `startDate` returns empty; the horizon honours `+24 months`.

## Why we take the latest run (and don't vintage)

Unlike the long-term **temperatures** (run-date-independent to ≤~0.005 °C), this profile **is**
run-date-dependent — probing `MEAN` at `runDate=2026-05-01` vs `2026-06-01` showed hours
differing by **hundreds of MW**. We deliberately **omit `runDate`** to take the **latest** run and
full-refresh weekly into the single-vintage `covariate` (the "actual" covariate store): each run
overwrites the prior view with the most up-to-date climatology. We are **not** vintaging this —
multi-vintage storage is `kpler_generation_forecast` + `forecast_covariate` (ADR 0009). Series
dropped from the dictionary as the 10-year window slides keep their stored rows (see
`load/upsert.py`).

## Shape

`fetch(since)` loops one request per (fuel, model) → tidy long frame `[zone, fuel, model,
date(ts), value]` (fuel = our code). `to_canonical(raw)` merges with the dictionary on
`(zone, fuel, model)` to attach `series_id`/`area`/`sub_group`/…, producing canonical rows
validated by `../../validation/generation.py` (MW sanity band, shared with
`kpler_generation_actual`). `load` routes them to `covariate` keyed by the full hourly timestamp.

## Cadence (production)

No orchestrator (ADR 0001). Schedule with a weekly cron line on the host:

```cron
0 6 * * 1 cd /path/to/gas-balance-v2 && uv run etl run kpler_generation_long_term >> /var/log/etl-kpler-gen-lt.log 2>&1
```

## Adding / changing areas

Add a `{area, zone}` line to `kpler_generation_long_term.yaml` (remember Germany is `DE-LU`); the
33 model×fuel series are generated in `connector.series_dict()`. To change the horizon or how many
weather years, edit `_HORIZON_MONTHS` / `_N_REF_YEARS` in `connector.py`.

## Test

`etl/tests/test_kpler_generation_long_term.py` — fixture-based, no live network. Covers the
dynamic model list (MEAN + last-10 REF), the `(zone, fuel, model)→canonical` mapping, the
unknown zone/fuel/model drop, and the MW sanity band. (Retry/backoff is shared and tested in
`test_kpler_http.py`.)
