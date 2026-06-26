# kpler_power_forward_curve — Kpler power price forward curve

Power-price **forward curves** per zone (EUR/MWh), built from EEX futures settlements — used as a
*predicted* exogenous **covariate** for gas-for-power demand (forward power prices drive gas
dispatch). One baseload series per zone, code `KP.PFC.<zone>`, `sub_group` = the `demandPeriod`
(`base`).

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Vintage** | `tradingDate` — the EEX settlement date the curve is built from (= `made_on`). |
| **Scenario / period** | `main` scenario, `base` (baseload) demand period — the reference forward. |
| **Granularity** | `daily` (~1.65k delivery days per zone/vintage, curve runs to ~2030). |
| **Refresh** | **Self-managing & backfills** — each run fetches the desired keep-set of trading dates not already stored (+ a 3-day refresh overlap for revised settlements). |
| **Cadence** | Daily (cron). |
| **Series** | 1 per balance area (`area` ↔ Kpler bidding `zone`) in `../../settings/kpler_power_forward_curve.yaml`; codes `KP.PFC.<zone>`. |
| **Coverage** | History begins ~2023; weekend/holiday trading dates have no settlement (0 rows). |
| **Units** | EUR/MWh. |
| **Stored in** | `forecast_covariate` (vintage-keyed `(series_id, made_on, ts)`) — see ADR 0009. |

## Endpoint

`GET power/prices/price-forward-curve/power?zones=<a>&zones=<b>&…&tradingDate=<D>&scenarios=main&demandPeriod=base&granularity=daily`
→ `{"data": [{startDate, value, currency, zone, scenario, tradingDate, granularity, demandPeriod, provider}]}`.

Endpoint quirks (probed live; they differ from the loads/generations forecasts):

- **`zones` batches every zone in one request** (`zones=FR&zones=DE-LU` → both), so a run is **one
  request per trading date**.
- **No `timezone` param** — sending it returns **HTTP 422** (the loads/generations endpoints
  require it; this one rejects it).
- **Germany is `DE-LU`** (the bidding-zone enum); plain `DE` → 422 (same as
  `kpler_generation_forecast`). **`DK` and `LT` are not in the enum** — they're omitted from the
  settings YAML.
- **`tradingDate` is required.** `scenarios` / `demandPeriod` / `granularity` default to
  `main` / `base` / `hourly`; we pass them explicitly and pick `daily`.

A run is `len(trading_dates)` GETs (~67 on the first backfill: 15 daily + 52 Mondays; a handful
incrementally), **fanned out concurrently** (`httpx.AsyncClient` + `arequest`, bounded by
`_CONCURRENCY`) over the shared 429/5xx retry/backoff.

## Vintage & retention

A forward curve is `(tradingDate, deliveryDate) → value`, so the same delivery day recurs in every
trading date's curve. Rows land in `forecast_covariate` keyed `(series_id, made_on, ts)`
(`made_on` = the trading date) — every vintage kept, not overwritten. Storage is bounded by the
shared **retention** rule enforced in the `load` hook (and re-runnable via
`etl prune kpler_power_forward_curve`): **keep all trading dates from the last 15 days + every
Monday for 1 year; delete the rest.** The fetch keep-set is exactly this set, so we never pull a
vintage we'd immediately prune. No history floor is needed (the curve's ~2023 history covers the
whole trailing-year window); weekend/holiday trading dates inside the window simply return 0 rows
and are harmlessly re-requested each run.

## Gas day

We store the **raw daily UTC** series. Any gas-day aggregation is applied **downstream in `ml/`**,
not here.

## Shape

`fetch(since)` fans out one request per trading date concurrently → a long frame
`[zone, date(ts), value, made_on]`. `to_canonical(raw)` merges the dictionary on `zone`, drops
unknown zones, carries `made_on`, and stamps the canonical columns — validated by
`forecast_covariate_power_price_schema` (a generous EUR/MWh band + `unique(made_on, date,
series_id)`). `load` routes the rows to `forecast_covariate` and prunes.

## Adding / changing areas

Add a line to `kpler_power_forward_curve.yaml` with an `area` (balance area) and `zone` (Kpler
bidding zone). Germany is `DE-LU`; the rest map 1:1. To pull `peak`/`off_peak` demand periods or
the weather/sensitivity scenarios, loop `_DEMAND_PERIOD` / `_SCENARIO` in the connector and tag
`sub_group`/code.

## Test

`etl/tests/test_kpler_power_forward_curve.py` — fixture-based, no live network. Covers the
zone → canonical mapping carrying `made_on`, multiple vintages of one delivery day coexisting,
dropping unknown zones, the EUR/MWh sanity band, the retention rule, and the fetch keep-set.
