# kpler_availability — Kpler actual plant availability

Daily **available capacity** (MW) per country, for the four thermal fuels relevant to the gas
balance — **coal, gas, lignite, nuclear** — used as exogenous covariates for gas-for-power
demand (when nuclear/coal capacity is out, gas fills the gap). We keep the `central` estimate;
the feed's `low`/`high` band (populated only for a few major markets) is skipped.

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Incremental & self-managing** — `fetch()` reads the last loaded `covariate` timestamp and pulls from there (minus a 7-day refresh overlap). First run backfills from `_HISTORY_START` (2016) in 365-day chunks, fanned out **async** (bounded by `_CONCURRENCY`). |
| **Cadence** | Daily (cron). |
| **Series** | 4 per balance area (`area` ↔ Kpler `zone`, identity map) in `../../settings/kpler_availability.yaml`; codes `KP.AVAIL.{COAL,GAS,LIGNITE,NUCLEAR}.<zone>`. |
| **Coverage** | All 18 zones, back to ~2016 (FR nuclear). Combos a country has no fleet for (e.g. AT nuclear, DE nuclear post-shutdown) carry a constant 0 — kept, harmless. |
| **Units** | MW. |
| **Stored in** | `covariate` (daily `ts`), **not** `observation` — see ADR 0008. |

## Endpoint

`GET power/outages/availability/fuel-types?zones=<all>&fuelTypes=<4>&granularity=daily&timezone=UTC&startDate=<D0>&endDate=<D1>`
→ `{"data": [{asOf, provider, zone, fuelType, startDate, low, central, high}]}`. `startDate`,
`endDate` and `zones` are **required**; `endDate` is **exclusive**. `zones` (plain **country
codes** — `DE`, not the `DE-LU` bidding zone) and `fuelTypes` both take the whole set in one
request. **`asOf` is the vintage param; omitting it returns the latest snapshot** — which for
past delivery dates is the realized "actual" availability (the forward/vintaged view is the
sibling `kpler_availability_forecast`). So we omit `asOf` and request delivery dates up to today.

## Shape

`fetch(since)` async-fetches the date chunks (all zones × all fuels each) → a tidy long frame
`[zone, fuelType, date(ts), value]` (`value` = `central`, dropping nulls). `to_canonical(raw)`
maps each `fuelType` to one of our four codes, drops unmapped fuels / unknown zones / nulls, and
merges the dictionary on `(zone, fuel)` — fuels are 1:1 (no folding), so a `(zone, fuel, day)` is
already unique. Canonical rows are validated by `../../validation/availability.py` (a
non-negative MW band — availability is genuinely ≥ 0). `load` routes them to `covariate` keyed by
the daily UTC timestamp.

## Adding / changing areas

Add a line to `kpler_availability.yaml` with an `area` (balance area) and `zone` (Kpler country
code). Today every area maps 1:1 to the identically-named zone.

## Test

`etl/tests/test_kpler_availability.py` — fixture-based, no live network. Covers the
(zone, fuel) → canonical mapping, dropping unmapped fuels / unknown zones / nulls, and the MW
sanity band (an absurd value is blocked; a plausible value and 0 pass; a negative is rejected).
