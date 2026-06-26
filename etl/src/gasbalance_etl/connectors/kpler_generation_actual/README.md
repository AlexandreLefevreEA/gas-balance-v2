# kpler_generation_actual — Kpler actual power generation

Hourly actual power generation (MW) per power zone, for four fuels relevant to the gas
balance — **solar, wind, run-of-river, gas** — used as exogenous covariates for
gas-for-power demand. Kpler splits wind into `wind onshore` + `wind offshore`; we **sum**
them into one WIND series. Every other fuel the endpoint returns (nuclear, coal, biomass,
…) is dropped.

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Incremental & self-managing** — `fetch()` reads the last loaded `covariate` timestamp and pulls from there (minus a 5-day refresh overlap). First run backfills from `_HISTORY_START` (2015) in 90-day chunks (a handful of requests). |
| **Cadence** | Daily (cron). |
| **Series** | 4 per balance area (`area` ↔ Kpler `zone`, identity map) in `../../settings/kpler_generation_actual.yaml`; codes `KP.GEN.{SOLAR,WIND,ROR,GAS}.<zone>`. |
| **Coverage** | All 18 zones return data (back to ~2016, deeper for FR/DE). Absent fuels (e.g. no offshore wind in landlocked zones) are simply not summed. |
| **Units** | MW. |
| **Stored in** | `covariate` (hourly `ts`), **not** `observation` (daily) — see ADR 0008. |

## Endpoint

`GET power/generations/fuel-types?zones=<all>&granularity=hourly&timezone=UTC&startDate=<D0>&endDate=<D1>`
→ `{"data": [{startDate, endDate, zone, fuelType, isFixed, value, provider}]}`. `startDate`
and `endDate` are **required**; `endDate` is **exclusive**. `zones` takes all areas in one
request, and a whole date window comes back in one (uncapped) response, so we fetch in
90-day chunks across all zones rather than one request per day.

## Gas day

We store the **raw hourly UTC** series. The EU **gas day runs 06:00→06:00 CET** (DST-aware:
05:00 UTC in winter, 04:00 UTC in summer), so gas-day aggregation is applied **downstream in
`ml/`**, not here. Kpler's own `daily` granularity is calendar-day (midnight→midnight) — the
wrong boundary — which is exactly why we keep hourly and let the model aggregate.

## Shape

`fetch(since)` sync-fetches the date chunks (all zones each) → a tidy long frame
`[zone, fuelType, date(ts), value]`. `to_canonical(raw)` maps each `fuelType` to one of our
four codes, drops nulls / unmapped fuels / unknown zones, merges the dictionary on
`(zone, fuel)`, then **sums** per `(zone, code, hour)` to fold `wind onshore` + `wind
offshore` into WIND — producing canonical rows validated by `../../validation/generation.py`
(a generous MW sanity band; the feed carries small metering-noise negatives, so we don't
floor at 0). `load` routes them to `covariate` keyed by the full hourly timestamp.

## Adding / changing areas

Add a line to `kpler_generation_actual.yaml` with an `area` (balance area) and `zone`
(Kpler zone). Today every area maps 1:1 to the identically-named zone. To aggregate a split
country later (e.g. Italy's bidding zones), change the connector to combine several zones
into one area.

## Test

`etl/tests/test_kpler_generation_actual.py` — fixture-based, no live network. Covers the
(zone, fuel) → canonical mapping, the wind onshore+offshore fold, dropping unmapped fuels /
unknown zones / nulls, and the MW sanity band (an absurd value is blocked; a small
metering-noise negative is allowed).
