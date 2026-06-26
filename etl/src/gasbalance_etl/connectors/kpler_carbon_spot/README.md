# kpler_carbon_spot — Kpler EU carbon (EUA) spot price

Daily EU Emissions Allowance (EUA) **spot settlement price** (EUR/tCO2), used as an exogenous
**covariate** for gas-for-power demand: the carbon price sets gas-vs-coal switching economics, so
it drives how much gas-fired generation runs. A **single** global EU series, code `KP.CARBON.SPOT`.

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Incremental & self-managing** — `fetch()` reads the last loaded `covariate` timestamp and pulls trading dates from there (minus a 5-day refresh overlap for settlement revisions). First run backfills from `_HISTORY_START` (2015). |
| **Cadence** | Daily (cron). |
| **Series** | **One** — `KP.CARBON.SPOT`, area `EU`, `group` = `carbon`, `sub_group` = `eua`. Hardcoded in `series_dict()` (no per-zone list to externalise). |
| **Coverage** | EUA spot from ~2015 (2014 returns empty). |
| **Units** | EUR/tCO2. |
| **Stored in** | `covariate` (daily `ts` at midnight UTC), **not** `observation` — see ADR 0008. |

## Endpoint

`GET power/prices/spot/emissions?tradingDate=<D>&provider=eex`
→ `{"data": [{root, tenor, longName, marketArea, deliveryStart, deliveryEnd, openPrice, highPrice,
lowPrice, lastPrice, settlementPrice, tradedVolume, unit, tradingDate, product, commodity,
provider}]}`.

Unlike the loads/generation endpoints, the **only** params are `tradingDate` (a single date,
**required**) and `provider` (default `eex`) — **no `zone`, no date range, no `granularity`** — so a
run is **one request per trading date** (no chunking). Each day returns the emissions products
traded; two `root` families appear:

- **`SEME`** = "EEX EUA Spot" (the EU Allowance carbon price) — present on **every** trading day,
  carries the real `lastPrice`/`tradedVolume`. **This is our series**; we take its `settlementPrice`.
- **`SEMA`** = the EU**AA** *aviation* allowance (zero volume) — **dropped**.

One SEME row per trading day (a rolling front contract; no intraday, no roll dups). Non-trading
days (weekends/holidays) return an empty list and contribute nothing.

Because one request = one trading date, a run is one GET per calendar day in the window (a handful
on an incremental run, ~4k on the first 2015-to-now backfill), **fanned out concurrently**
(`httpx.AsyncClient` + `arequest`, bounded by `_CONCURRENCY`) over the shared 429/5xx retry/backoff.

## Shape

`fetch(since)` fans out one request per trading date concurrently → a tidy long frame
`[date, root, value]` (`value` = `settlementPrice`). `to_canonical(raw)` keeps `root == "SEME"`,
drops null settlements, stamps the single series's canonical columns — validated by
`../../validation/carbon.py` (a generous EUR/tCO2 band). `load` routes the rows to `covariate`
keyed by the daily timestamp.

## Test

`etl/tests/test_kpler_carbon_spot.py` — fixture-based, no live network. Covers the single-series
mapping, keeping SEME over SEMA (EUAA), dropping null settlements, and the EUR/tCO2 sanity band (an
absurd value is blocked).
