# kpler_long_term_temperatures — Kpler long-term temperatures (normals + weather years)

Hourly population-weighted 2 m temperature (°C) per power zone, **forward-looking
climatology** used as a demand covariate — distinct from the day-ahead actuals in
`kpler_actual_temps`. Selected via Kpler's `baseWeatherModel` param:

- **MEAN** — the "normal" temperature profile.
- **REF_YYYY** — the profile if that historical year's weather recurred ("weather years").
  We pull the **last 10 completed years**, recomputed each run (today `REF_2016 … REF_2025`).

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (shared with `kpler_actual_temps`; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Full, weekly.** Each run pulls the forward window `[today, today + 24 months]` for every model and upserts idempotently. |
| **Cadence** | Weekly (cron). |
| **Series** | 11 per balance area (MEAN + last-10 REF) — zones in `../../settings/kpler_long_term_temperatures.yaml`. Codes `KP.TEMPLT.<zone>.<MODEL>`. |
| **Units** | °C (`degC`). |
| **Stored in** | `covariate` (hourly `ts`), **not** `observation` (daily) — see ADR 0008. |

## Endpoint

`GET power/loads/forecasts/temperature/long-term?zones=<all>&baseWeatherModel=<M>&granularity=hourly&timezone=UTC&startDate=<today>&endDate=<+24mo>`
→ `{"data": [{zone, scenario:"no_scenario", startDate, value, provider, runDate}]}`.
The `zones[]` param returns **all areas in one request**, so a run is one request per
model (11 total). The response does **not** echo `baseWeatherModel`, so the connector
tags each request's rows with the model it asked for.

## Why no runDate (run-date-independence)

The long-term normals are essentially run-date-independent: probing `MEAN` at
`runDate=2026-05-01` vs `2026-06-01` showed 23/24 hours differing by only **≤~0.005 °C**
(third decimal). So we omit `runDate` (take the latest run) and full-refresh weekly —
no per-runDate vintaging. Series dropped from the dictionary as the 10-year window slides
keep their stored rows (see `load/upsert.py`).

## Shape

`fetch(since)` loops one request per model → tidy long frame `[zone, model, date(ts),
value]`. `to_canonical(raw)` merges with the dictionary on `(zone, model)` to attach
`series_id`/`area`/`sub_group`/…, producing canonical rows validated by
`../../validation/temperature.py` (plausible-range guard). `load` routes
them to `covariate` keyed by the full hourly timestamp.

## Cadence (production)

No orchestrator (ADR 0001). Schedule with a weekly cron line on the host:

```cron
0 6 * * 1 cd /path/to/gas-balance-v2 && uv run etl run kpler_long_term_temperatures >> /var/log/etl-kpler-lt.log 2>&1
```

## Adding / changing areas

Add a `{area, zone}` line to `kpler_long_term_temperatures.yaml`; the 11 model series are
generated in `connector.series_dict()`. To change the horizon or how many weather years,
edit `_HORIZON_MONTHS` / `_N_REF_YEARS` in `connector.py`.

## Test

`etl/tests/test_kpler_long_term_temperatures.py` — fixture-based, no live network. Covers
the dynamic model list (MEAN + last-10 REF), the `(zone, model)→canonical` mapping, the
unknown zone/model drop, the temperature-range gate, and the 429/5xx retry.
