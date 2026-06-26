# kpler_carbon_settles — EU carbon (EUA) futures settlement curve

EEX emissions **futures settlement** prices (EUR/tCO2) per trading date — the raw monthly
**EU Allowance (EUA, ETS1)** anchor points. Stored as a vintage-keyed forecast covariate; the
`carbon_curve` transform later splines these (plus the EUA spot) into a daily forward curve. One EU
series, code `KP.CARBON.SETTLES`, `sub_group` = `eua_settles`.

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; + `KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Vintage** | `tradingDate` — the EEX settlement date (= `made_on`); `ts` = contract `maturityDate`. |
| **Filter** | `longName == "EEX EUA Future"` (drops `EEX EU ETS2 Future` and `EEX UKA Futures`); `maturityType == "month"`; non-null `settlementPrice`. |
| **Refresh** | **Self-managing & backfills** — each run fetches the desired keep-set of trading dates not already stored (+ a 3-day refresh overlap for revised settlements). |
| **Cadence** | Daily (cron). |
| **Series** | 1 (hardcoded, no YAML), code `KP.CARBON.SETTLES`. |
| **Coverage** | EUA futures history is deep (anchors returned for 2023→now); weekend/holiday trading dates have no settlement (0 rows). |
| **Units** | EUR/tCO2. |
| **Stored in** | `forecast_covariate` (vintage-keyed `(series_id, made_on, ts)`) — see ADR 0009. |

## Endpoint

`GET power/prices/futures/settlements/emissions?tradingDate=<D>&provider=eex`
→ `{"data": [{flowDate, product, contractName, longName, maturityDate, maturityType,
settlementPrice, unit, provider, …}]}`.

Endpoint quirks (probed live):

- Params: `tradingDate` (required) + `provider` (default `eex`). **One request per trading date**
  (no zone / no date range), fanned out concurrently (`httpx.AsyncClient` + `arequest`, bounded by
  `_CONCURRENCY`) over the shared 429/5xx retry/backoff.
- Each date returns **three** product families, all `maturityType == "month"`: `EEX EUA Future`
  (EUR/tCO2, EU ETS1 — **kept**), `EEX EU ETS2 Future` (EUR/EUA2) and `EEX UKA Futures` (GBP/UKA) —
  the latter two dropped by the `longName` filter.
- `maturityDate` is the 1st of the contract month; the front contract can predate `tradingDate`.
  All EUA anchors are stored as-is — the `carbon_curve` transform drops past maturities when it
  builds the spline.
- A transient `502`/`429` is handled by the shared `arequest` retry.

## Vintage & retention

`(tradingDate, maturityDate) → value`, so the same contract recurs in every trading date's strip.
Rows land in `forecast_covariate` keyed `(series_id, made_on, ts)` (`made_on` = trading date) —
every vintage kept, not overwritten. Storage is bounded by the shared **retention** rule enforced
in the `load` hook (and re-runnable via `etl prune kpler_carbon_settles`): **keep all trading dates
from the last 15 days + every Monday for 1 year; delete the rest.** The fetch keep-set is exactly
this set, so we never pull a vintage we'd immediately prune. No history floor is needed.

## Shape

`fetch(since)` fans out one request per trading date → a long frame `[date(maturity), value,
made_on(tradingDate)]`. `to_canonical(raw)` stamps the single-series metadata and carries `made_on`
(no interpolation — anchors are stored raw) — validated by `forecast_covariate_carbon_schema` (the
EUR/tCO2 `[0, 1000]` band + `unique(made_on, date, series_id)`). `load` routes the rows to
`forecast_covariate` and prunes.

## Test

`etl/tests/test_kpler_carbon_settles.py` — fixture-based, no live network. Covers the EUA-only
filter (ETS2 / UKA dropped), `made_on` carried, multiple vintages of one maturity coexisting, the
EUR/tCO2 sanity band, the retention rule, and the fetch keep-set.
