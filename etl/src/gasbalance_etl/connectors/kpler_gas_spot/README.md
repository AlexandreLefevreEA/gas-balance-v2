# kpler_gas_spot — Kpler gas day-ahead spot price (covariate)

Daily EEX **gas day-ahead settlement** price (EUR/MWh) per EU gas hub — a price covariate
for gas-for-power demand (a high gas price makes gas plants the marginal switch).

| | |
|---|---|
| **Source** | `GET /power/prices/spot/gas` (Kpler v2), HTTP Basic auth (`KPLER_API_KEY_V2`, shared) |
| **Params** | `marketArea` (enum, one per request), `tradingDate` (one day per request); `provider` optional (defaults to EEX) |
| **Cadence** | daily (one settlement per trading day per hub); **incremental & self-managing** |
| **Series** | one per hub, code `KP.GASSPOT.<marketArea>`, `group=price`, `sub_group=gas_spot`, unit EUR/MWh |
| **Storage** | `covariate` table (full timestamp), indexed by **trading date**; gas-day alignment is downstream in `ml/` |
| **Validation** | `validation/gas_spot.py` — EUR/MWh sanity band |
| **Zones/areas** | `settings/kpler_gas_spot.yaml` |

## What value we keep

The endpoint returns several products per day keyed by `tenor`. We keep the canonical
**day-ahead "DAY 1 MW"** record (`tenor == "day_ahead"`, `longName` ending `DAY 1 MW` — the
next-gas-day delivery) and take its `settlementPrice`, falling back to `lastPrice` when the
settlement isn't published yet (the current day, or a thin holiday). The within-day and
weekend (`SAT MW` / `SUN MW`) legs and the duplicate named spot-index root (`<HUB>DA`, same
price) are dropped. See `_day1_value`.

## Gotchas

- **Per market area + per trading date** — no date-range or zones-batch param. A run fans out
  `marketAreas × weekday tradingDates` requests (weekends don't trade → skipped).
- **Units differ by hub.** TTF/THE/PEG/etc. are EUR/MWh; **NBP** is `p/kthm` (UK pence/therm)
  and is excluded. **GPL, NCG, ZEE** return no day-ahead and are excluded. The market-area
  enum also includes these — only the 11 EUR/MWh day-ahead hubs are listed in the YAML.
- **Shallow history.** TTF day-ahead starts ~2020; THE ~2023, PEG ~2021. Earlier/absent
  hub-days return empty and drop. `_HISTORY_START = 2020-01-01`.
- **Provisional current-day price.** Today's settlement is usually unpublished, so today's
  value is the last trade until the next run's `_REFRESH_DAYS` overlap overwrites it with the
  settled price.
