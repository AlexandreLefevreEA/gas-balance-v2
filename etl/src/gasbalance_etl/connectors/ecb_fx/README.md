# ecb_fx — ECB euro FX reference rates (covariate)

Daily ECB **euro foreign-exchange reference rates** (units of foreign currency per 1 EUR) for a
curated set of currencies — price/supply covariates for the gas balance (USD for LNG/oil, GBP
for the UK NBP hub, NOK for Norwegian pipeline supply).

| | |
|---|---|
| **Source** | `GET https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip` — **public, no auth** |
| **Format** | a ZIP holding one wide CSV: `Date` + one column per currency, history from 1999 |
| **Cadence** | daily; **full refresh** (the hist file is the whole history; the upsert is idempotent) |
| **Series** | one per currency, code `ECB.FX.<currency>`, `group=fx`, `sub_group=spot`, unit `<CCY>/EUR` |
| **Storage** | `covariate` table, each rate keyed by a midnight-UTC `ts` (ADR 0008) |
| **Validation** | `validation/fx.py` — `fx_schema`, a `(0, 1000]` gross-error band |
| **Currencies** | `settings/ecb_fx.yaml` (USD, GBP, NOK) |

## Direction (no business logic)

Rates are stored **as the ECB publishes them**: foreign per 1 EUR (e.g. `USD` ~1.08 = 1 EUR →
1.08 USD). The connector only fetches + maps + validates; inversion to EUR-per-foreign belongs
downstream in `ml/`. The recorded unit (`<CCY>/EUR`) makes the direction explicit.

## Gotchas

- **Wide CSV, trailing comma.** The header ends with a comma, so pandas reads a trailing
  `Unnamed` column — `_parse` drops any `Unnamed*` column before melting.
- **Blanks / `N/A`.** A currency not quoted on a given day (e.g. a discontinued currency, or a
  holiday) is a blank or `N/A` cell → coerced to NaN and dropped. The three curated currencies
  are quoted every TARGET business day, so this mostly affects currencies you don't keep.
- **Add a currency** by adding a row to `settings/ecb_fx.yaml`. If it is a high-magnitude rate
  (JPY ~170, HUF ~390, IDR ~17000 per EUR), raise the `fx_schema` ceiling above 1000 too.
- **No incremental window.** Every run re-downloads and re-loads the full history; this is
  intentional (one small file, idempotent upsert) — there is no `since`/last-loaded logic.

## Test

`etl/tests/test_ecb_fx.py` — fixture-based, no live network, no DB. Covers the wide-CSV parse
(trailing `Unnamed` column, `N/A`/blank dropping), the currency → canonical mapping, dropping
unknown currencies / nulls, and the `(0, 1000]` band.
