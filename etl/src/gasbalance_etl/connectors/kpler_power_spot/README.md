# kpler_power_spot — Kpler day-ahead power prices

Hourly day-ahead electricity **spot price** (EUR/MWh) per power zone, used as an exogenous
**covariate** for gas-for-power demand (high power prices → gas plants are in the money and
run). One series per zone, code `KP.SPOT.<zone>`, `sub_group` = `day_ahead`.

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Incremental & self-managing** — `fetch()` reads the last loaded `covariate` timestamp and pulls from there (minus a 5-day refresh overlap). First run backfills from `_HISTORY_START` (2016). |
| **Cadence** | Daily (cron). |
| **Series** | 1 per balance area (`area` → Kpler bidding `zone`) in `../../settings/kpler_power_spot.yaml`; code `KP.SPOT.<zone>`. |
| **Coverage** | All 18 zones return data from ~2016 (the API floors `startDate` at 2014; 2015 is empty). |
| **Units** | EUR/MWh (GB is normalised to EUR by Kpler — no currency split). |
| **Stored in** | `covariate` (hourly `ts`), **not** `observation` (daily) — see ADR 0008. |

## Endpoint

`GET power/prices/day-ahead?zones=<a>&zones=<b>…&granularity=hourly&timezone=UTC&startDate=<D0>&endDate=<D1>`
→ `{"data": [{startDate, zone, value, currency, isFixed, provider}]}`.

- **`zones` batches** — pass the whole zone set in one request and the response carries every
  zone (one request per date chunk, all zones at once — like `kpler_generation_actual`, unlike
  `kpler_power_demand`'s singular `zone`).
- **`zones` is the ENTSO-E bidding-zone enum** (like `kpler_generation_forecast`, NOT the
  loads/country-code family). Germany is **`DE-LU`** (not `DE`); split countries take a
  sub-zone — Denmark **`DK1`** (West), Italy **`IT-NORTH`** (Italy's national `IT-PUN` is a
  valid enum value but returns **no data**). The area→zone map in the YAML handles these.
- A request's `startDate` floors at **2014-01-01** (`endDate` exclusive). We fetch in
  `_CHUNK_DAYS` (1-year) chunks — only the first backfill needs more than one; incremental
  runs span days.
- **Day-ahead prices go negative** (renewable oversupply) and spike high (EU caps ~+4000/5000).
- Very recent hours may omit `value` (handled — dropped as null).

A run is `chunks` GETs (1 incremental, ~11 on first backfill), **fanned out concurrently**
(`httpx.AsyncClient` + `arequest`, bounded by `_CONCURRENCY`) over the shared 429/5xx retry.

## Gas day

We store the **raw hourly UTC** series. The EU **gas day runs 06:00→06:00 CET** (DST-aware),
so gas-day aggregation is applied **downstream in `ml/`**, not here.

## Shape

`fetch(since)` fans out one request per date chunk (all zones batched) → a tidy long frame
`[zone, date(ts), value]`. `to_canonical(raw)` merges the dictionary on `zone`, drops nulls /
unknown zones, and stamps the canonical columns — validated by `../../validation/spot_price.py`
(a generous EUR/MWh band). `load` routes the rows to `covariate` keyed by the full hourly
timestamp.

## Adding / changing areas

Add a line to `kpler_power_spot.yaml` with an `area` (balance area) and `zone` (Kpler bidding
zone). This endpoint uses the bidding-zone enum, so a country with split zones needs the
specific sub-zone (e.g. `DE-LU`, `DK1`, `IT-NORTH`), not the country code.

## Test

`etl/tests/test_kpler_power_spot.py` — fixture-based, no live network. Covers the zone →
canonical mapping (incl. the DE-LU / IT-NORTH remaps), dropping unknown zones / nulls, and the
EUR/MWh band (a negative price passes; an absurd value is blocked).
