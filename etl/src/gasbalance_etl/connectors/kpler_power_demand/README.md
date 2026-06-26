# kpler_power_demand — Kpler actual power demand

Hourly actual electricity demand (total system load, MW) per power zone, used as an
exogenous **covariate** for gas-for-power demand (high load → more gas plants run). One
series per zone, code `KP.LOAD.<zone>`, `sub_group` = the Kpler `loadType` (`demand`).

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Incremental & self-managing** — `fetch()` reads the last loaded `covariate` timestamp and pulls from there (minus a 5-day refresh overlap). First run backfills from `_HISTORY_START` (2015). |
| **Cadence** | Daily (cron). |
| **Series** | 1 per balance area (`area` ↔ Kpler `zone`, identity map) in `../../settings/kpler_power_demand.yaml`; code `KP.LOAD.<zone>`. |
| **Coverage** | All 18 zones return data (most from ~2016, FR from 2014). |
| **Units** | MW. |
| **Stored in** | `covariate` (hourly `ts`), **not** `observation` (daily) — see ADR 0008. |

## Endpoint

`GET power/loads/actual?zone=<one>&loadType=demand&granularity=hourly&timezone=UTC&startDate=<D0>&endDate=<D1>`
→ `{"data": [{startDate, zone, value, isFixed, provider}]}`.

Differences from `kpler_generation_actual`'s endpoint:

- **`zone` is singular** (one zone per request) — `zones[]` 422s — so we loop zones. The zone
  enum is the ENTSO-E bidding-zone set, but plain **country codes work** for all 18 of our
  areas (Germany is accepted as `DE`, no `DE-LU` needed here).
- **`loadType`** ∈ {`demand`, `residual_demand`}; we pull total `demand`. `residual_demand`
  (load net of renewables) is a one-line add: loop `_LOAD_TYPE` and tag `sub_group`/code.
- A request's date range may **not exceed 12 years** (`endDate` exclusive). One uncapped
  zone-decade is ~88k rows, so we fetch in `_CHUNK_DAYS` (~10-year) chunks — only the first
  backfill needs more than one chunk; incremental runs span days.

Because one request = one zone, a run is `zones x chunks` GETs (18 incremental, ~36 on first
backfill), **fanned out concurrently** (`httpx.AsyncClient` + `arequest`, bounded by
`_CONCURRENCY`) over the shared 429/5xx retry/backoff.

## Gas day

We store the **raw hourly UTC** series. The EU **gas day runs 06:00→06:00 CET** (DST-aware),
so gas-day aggregation is applied **downstream in `ml/`**, not here.

## Shape

`fetch(since)` fans out one request per (zone × chunk) concurrently → a tidy long frame
`[zone, date(ts), value]`. `to_canonical(raw)` merges the dictionary on `zone`, drops nulls /
unknown zones, and stamps the canonical columns — validated by `../../validation/demand.py`
(a generous MW band). `load` routes the rows to `covariate` keyed by the full hourly
timestamp.

## Adding / changing areas

Add a line to `kpler_power_demand.yaml` with an `area` (balance area) and `zone` (Kpler
zone). Today every area maps 1:1 to the identically-named zone. To aggregate a split country
later (e.g. Italy's bidding zones), change the connector to combine several zones into one
area.

## Test

`etl/tests/test_kpler_power_demand.py` — fixture-based, no live network. Covers the zone →
canonical mapping, dropping unknown zones / nulls, and the MW sanity band (an absurd value is
blocked; a plausible load passes).
