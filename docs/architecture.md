# Architecture

## Purpose

Forecast the EU natural-gas supply/demand balance: per-country/per-zone demand,
supply, flows and storage, projected ~2 years forward, in multiple scenarios — and
make those numbers **queryable, trustworthy, and cheap to re-experiment on**.

## The four parts (separation of concerns)

```
                    ┌──────────────────────────────────────────────┐
   data sources ───▶│  etl/   fetch → transform → VALIDATE → load   │───▶ Postgres
   (per connector)  └──────────────────────────────────────────────┘        │
                                                                              │
                    ┌──────────────────────────────────────────────┐         │
                    │  ml/    features ← read                       │◀────────┤
                    │         fit / backtest / forecast → write     │────────▶│ (forecasts)
                    └──────────────────────────────────────────────┘         │
                                                                              │
                    ┌─────────────────┐         ┌────────────────┐           │
        browser ───▶│  web/ React+Vite│ ──────▶ │  api/ FastAPI  │◀──────────┘
                    └─────────────────┘  HTTP   └────────────────┘   reads
```

- **etl/** — one independent connector per data source. Each fetches its raw data,
  maps it to the canonical series schema, is **validated** (Pandera) before it can
  land, and loads into Postgres. Sources can be added/swapped/run in isolation.

  **Sources**
  - **ce** (Commodity Essentials) — European gas fundamentals (flows, storage, demand,
    supply). HTTP Basic Auth, CSV `eugasseries` endpoint; **full refresh** hourly
    (re-fetch history since 2014, idempotent upsert); curated series dictionary in
    `etl/src/gasbalance_etl/settings/ce.yaml`. The first connector — it also bootstraps
    the shared CLI (`etl run <source>`), the load/upsert step, and the canonical Pandera
    schema, all kept source-agnostic. (ADR 0003.)
  - **kpler_actual_temps** (Kpler) — hourly actual temperature (°C) per power zone, a
    demand **covariate**. Kpler has no observed product, so "actual" = the day-ahead
    (D-1) slice of each archived 00z EC_OP run (consistent back to ~2018). HTTP Basic
    Auth, JSON; **incremental** (self-managed from the last loaded timestamp; first run
    backfills). Hourly, so it lands in the `covariate` table — keyed by timestamp,
    separate from the daily `observation` actuals — via the connector's `load` hook.
    Areas map 1:1 to Kpler zones in `settings/kpler_actual_temps.yaml`. (ADR 0008.)
  - **kpler_long_term_temperatures** (Kpler) — hourly forward-looking temperature
    **climatology** per power zone, a demand **covariate**, from
    `…/temperature/long-term`. Two flavours via `baseWeatherModel`: **MEAN** (the normal)
    and **REF_YYYY** weather years (last 10 completed years, recomputed each run) — so 11
    series per area, codes `KP.TEMPLT.<zone>.<MODEL>`. HTTP Basic Auth (shared Kpler key),
    JSON; **full refresh weekly** of the forward window `[today, +24 months]` (profiles are
    run-date-independent, so `runDate` is omitted). Hourly → lands in the `covariate` table
    via the `load` hook. Zones in `settings/kpler_long_term_temperatures.yaml`. (ADR 0008.)
  - **kpler_temps_forecast** (Kpler) — hourly temperature **forecasts** per power zone, the
    first **forecast covariate**, from the base `…/forecasts/temperature` endpoint. Two 00z
    models — **EC_AIFS_ENS** (AI ensemble, ~15-day) and **EC_46** (46-day extended) —
    ensemble-mean value; 2 series per area, codes `KP.TEMPFC.<zone>.<MODEL>`. A forecast has
    a **vintage** dimension (`runDate`) the actuals don't, so it lands in a new
    **`forecast_covariate`** table keyed `(series_id, made_on, ts)`, validated by
    `forecast_covariate_temperature_schema` (`unique(made_on, date, series_id)`) — see ADR
    0009. **Self-managing & backfills**: each run fetches the desired keep-set of run dates
    not already stored (+ a recent refresh overlap). A **retention** rule (keep last 15 days
    of runs + every Monday for 1 year) runs in the `load` hook and via `etl prune
    kpler_temps_forecast`. Zones in `settings/kpler_temps_forecast.yaml`. (ADR 0009.)
  - **kpler_generation_actual** (Kpler) — hourly actual power generation (MW) by fuel per
    power zone, an exogenous **covariate** for gas-for-power demand, from
    `…/power/generations/fuel-types`. Four fuels — **solar, wind, run-of-river, gas** (Kpler's
    `wind onshore` + `wind offshore` are summed into one WIND series); 4 series per area,
    codes `KP.GEN.{SOLAR,WIND,ROR,GAS}.<zone>`. HTTP Basic Auth (shared Kpler key), JSON;
    **incremental** (self-managed from the last loaded timestamp; first run backfills from
    2015 in 90-day chunks, all zones per request). Hourly → lands in the `covariate` table via
    the `load` hook. We store raw hourly UTC; the EU **gas-day (06:00 CET)** aggregation is
    applied downstream in `ml/` (Kpler's own `daily` is calendar-day, the wrong boundary).
    Zones in `settings/kpler_generation_actual.yaml`. (ADR 0008.)
  - **kpler_generation_forecast** (Kpler) — the **forecast** counterpart of
    `kpler_generation_actual`, kept per vintage, from `…/power/generations/forecasts`. Same
    four fuels (**solar, wind, run-of-river, gas**; `wind onshore` + `wind offshore` folded)
    and the two 00z models of `kpler_temps_forecast` — **EC_AIFS_ENS** (~15-day) and **EC_46**
    (46-day, ~1-day publish lag); one series per (zone × fuel × model), codes
    `KP.GENFC.<FUEL>.<zone>.<MODEL>`, `sub_group` = fuel. Like `kpler_temps_forecast` it has a
    **vintage** dimension, so it lands in **`forecast_covariate`** keyed `(series_id, made_on,
    ts)`, validated by `forecast_covariate_generation_schema` (`unique(made_on, date,
    series_id)`). **Self-managing & backfills** the keep-set (floored at `_HISTORY_START`
    ≈ Jan 2026, where the data begins) + a refresh overlap; same **retention** (last 15 days +
    every Monday for a year) via the `load` hook and `etl prune kpler_generation_forecast`.
    The endpoint takes **one zone + one fuelType per request** (`run=00z`; only `models` is a
    list, and `zones` is an ENTSO-E **bidding-zone** enum — Germany is `DE-LU`, not `DE`), so
    fetch issues one request per (run date × zone × fuel), **fanned out concurrently** (bounded
    by `_CONCURRENCY`). Zones in `settings/kpler_generation_forecast.yaml`. (ADR 0009.)
  - **kpler_generation_long_term** (Kpler) — hourly forward-looking **renewable** generation
    (MW) **climatology** per power zone, a covariate, from `…/power/generations/forecasts/long-term`
    — the generation analogue of `kpler_long_term_temperatures`. Two flavours via
    `baseWeatherModel`: **MEAN** (the normal) and **REF_YYYY** weather years (last 10 completed
    years, recomputed each run). The endpoint's `fuelType` enum is exactly the three renewables —
    **solar, wind, run-of-river** (no gas; **wind is one fuel**, no onshore/offshore split) — one
    per request, while `zones[]` batches every area, so a run is `fuels × models` (= 3 × 11 = 33)
    requests, **fanned out concurrently** (bounded by `_CONCURRENCY`). 33 series per area, codes
    `KP.GENLT.<FUEL>.<zone>.<MODEL>`, `sub_group` = fuel
    (`zones` is the ENTSO-E **bidding-zone** enum — Germany is `DE-LU`). HTTP Basic Auth (shared
    Kpler key), JSON; **full refresh weekly** of the forward window `[today, +24 months]`. Unlike
    the long-term temperatures this profile is **not** run-date-independent (it shifts by hundreds
    of MW across run dates), so we deliberately omit `runDate` to take the **latest** run and
    overwrite the **single-vintage `covariate`** in place — we do not vintage it (that's
    `kpler_generation_forecast`). Validated by the shared `generation_schema` (MW band). Zones in
    `settings/kpler_generation_long_term.yaml`. (ADR 0008.)
  - **kpler_availability** (Kpler) — daily actual plant **availability** (available capacity, MW)
    by fuel per country, an exogenous **covariate** for gas-for-power demand (when nuclear/coal
    capacity is out, gas fills the gap), from `…/power/outages/availability/fuel-types`. Four
    thermal fuels — **coal, gas, lignite, nuclear**; 4 series per area, codes
    `KP.AVAIL.{COAL,GAS,LIGNITE,NUCLEAR}.<zone>`, `sub_group` = fuel. We keep the `central`
    estimate (the feed's `low`/`high` band, populated only for a few major markets, is skipped).
    HTTP Basic Auth (shared Kpler key), JSON; **incremental** (self-managed from the last loaded
    timestamp; first run backfills from 2016 in 365-day chunks, all zones × fuels per request,
    **fanned out concurrently**). `zones` + `fuelTypes` batch in one request and `zones` is the
    **country-code** enum — `DE`, not `DE-LU`. **`asOf` is the vintage param; omitting it returns
    the latest snapshot** = the realized "actual" of past dates (the forward/vintaged view is
    `kpler_availability_forecast`). Daily → lands in the `covariate` table via the `load` hook.
    Validated by `availability_schema` (a non-negative MW band — availability is genuinely ≥ 0).
    Zones in `settings/kpler_availability.yaml`. (ADR 0008.)
  - **kpler_availability_forecast** (Kpler) — the **forecast** counterpart of
    `kpler_availability`, kept per vintage, from the same `…/power/outages/availability/fuel-types`
    with the **`asOf`** param. Each `asOf` snapshot captures the planned-outage outlook (delivery
    dates from `asOf` forward over a `_HORIZON_DAYS` ≈ 12-month window); 4 series per area, codes
    `KP.AVAILFC.{COAL,GAS,LIGNITE,NUCLEAR}.<zone>`, `sub_group` = fuel (**no model dimension**).
    The **vintage** is `asOf`, so rows land in **`forecast_covariate`** keyed `(series_id, made_on,
    ts)`, validated by `forecast_covariate_availability_schema` (`unique(made_on, date,
    series_id)`). **Self-managing & backfills** the keep-set (no history floor — `asOf` snapshots
    go back to ~2024, past the trailing-year keep-set) + a refresh overlap; same **retention**
    (last 15 days + every Monday for a year) via the `load` hook and `etl prune
    kpler_availability_forecast`. `zones` + `fuelTypes` batch in one request, so a run is **one
    request per `asOf`**, **fanned out concurrently** (bounded by `_CONCURRENCY`). Zones in
    `settings/kpler_availability_forecast.yaml`. (ADR 0009.)
  - **kpler_power_demand** (Kpler) — hourly actual electricity **demand** (total system load,
    MW) per power zone, an exogenous **covariate** for gas-for-power demand (high load → more
    gas plants run), from `…/power/loads/actual`. One series per area, code `KP.LOAD.<zone>`,
    `sub_group` = the Kpler `loadType` (`demand`; `residual_demand` is a one-line add). HTTP
    Basic Auth (shared Kpler key), JSON; **incremental** (self-managed from the last loaded
    timestamp; first run backfills from 2015). The endpoint takes **one zone per request**
    (`zone` is singular; plain country codes work — `DE`, not `DE-LU`) and caps a request at a
    **12-year range**, so a run is `zones × ~10-year chunks` requests, **fanned out
    concurrently** (bounded by `_CONCURRENCY`). Hourly → lands in the
    `covariate` table via the `load` hook; raw hourly UTC stored, gas-day aggregation applied
    downstream in `ml/`. Validated by `demand_schema` (MW band). Zones in
    `settings/kpler_power_demand.yaml`. (ADR 0008.)
  - **kpler_carbon_spot** (Kpler) — daily EU carbon (EUA) **emissions spot price** (EUR/tCO2), an
    exogenous **covariate** for gas-for-power demand (the carbon price sets gas-vs-coal switching
    economics), from `…/power/prices/spot/emissions`. A **single** global EU series, code
    `KP.CARBON.SPOT` (`group` = `carbon`, `sub_group` = `eua`) — no per-zone list, so the series is
    hardcoded in `series_dict()`. HTTP Basic Auth (shared Kpler key), JSON; **incremental**
    (self-managed from the last loaded timestamp; first run backfills from 2015). The endpoint's
    only params are `tradingDate` (one date, required) and `provider` (default `eex`) — **no zone,
    no date range** — so a run is **one request per trading date**, **fanned out concurrently**
    (bounded by `_CONCURRENCY`). Each day we keep `root == SEME` ("EEX EUA Spot", present every
    trading day) and take its `settlementPrice`; `root == SEMA` (the EUAA *aviation* allowance) is
    dropped. Daily settlement → the single-vintage `covariate` table (midnight-UTC `ts`) via the
    `load` hook. Validated by `carbon_schema` (EUR/tCO2 band). (ADR 0008.)
  - **kpler_gas_spot** (Kpler) — daily EEX **gas day-ahead spot price** (EUR/MWh) per EU gas hub,
    an exogenous **covariate** for gas-for-power demand (the gas price sets gas-vs-coal switching
    economics), from `…/power/prices/spot/gas`. One series per market area, code
    `KP.GASSPOT.<marketArea>` (`group` = `price`, `sub_group` = `gas_spot`); the **11 EUR/MWh
    day-ahead hubs** — TTF (NL), THE (DE), PEG (FR), PVB (ES), CEGH (AT), OTE (CZ), ZTP (BE), FIN,
    LTU, LVA-EST, ETF — in `settings/kpler_gas_spot.yaml`. The params are `marketArea` (enum, one
    per request) + `tradingDate` (one date, required) + optional `provider` (default `eex`) — **no
    date range, no zones-batch** — so a run is **marketAreas × weekday trading dates** requests
    (continental day-ahead doesn't trade weekends), **fanned out concurrently** (bounded by
    `_CONCURRENCY`). Each day we keep the day-ahead **"DAY 1 MW"** record (`tenor = day_ahead`,
    `longName` ending `DAY 1 MW`) and take its `settlementPrice`, falling back to `lastPrice` when
    unsettled (current day / thin holiday); the within-day, weekend (SAT/SUN MW) legs and the
    duplicate named spot-index root (`<HUB>DA`) are dropped. **NBP** (p/therm) and **GPL/NCG/ZEE**
    (no day-ahead) are excluded. Daily settlement, indexed by **trading date** → the single-vintage
    `covariate` table (midnight-UTC `ts`) via the `load` hook; first run backfills from 2020.
    Validated by `gas_spot_schema` (EUR/MWh band). (ADR 0008.)
  - **ecb_fx** (ECB) — daily euro **FX reference rates** (units of foreign currency per 1 EUR),
    a price/supply **covariate** (USD for LNG/oil, GBP for the UK NBP hub, NOK for Norwegian
    pipeline supply), from the **public** file
    `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip` — **no auth**. One series per
    currency, code `ECB.FX.<currency>` (`group` = `fx`, `sub_group` = `spot`, `unit` =
    `<CCY>/EUR`); the kept currencies — USD, GBP, NOK — in `settings/ecb_fx.yaml`. Rates are
    stored **as published** (foreign per EUR); inversion is a downstream `ml/` concern. The ZIP
    holds one wide CSV (`Date` + a column per currency, history from 1999); `fetch()` is a
    **single** GET (no async), `_parse` drops the trailing `Unnamed` column and `N/A`/blank cells
    and melts to long. **Full refresh** every run — the file is the whole history and the upsert
    is idempotent, so there is no incremental window. Daily rate, indexed by date → the
    single-vintage `covariate` table (midnight-UTC `ts`) via the `load` hook. Validated by
    `fx_schema` (a `(0, 1000]` gross-error band). (ADR 0008.)
  - **kpler_power_demand_forecast** (Kpler) — the **forecast** counterpart of
    `kpler_power_demand`, kept per vintage, from `…/power/loads/forecasts`. Hourly electricity
    demand (total load, MW) **forecasts** per power zone, the two 00z models of the other
    forecast covariates — **EC_AIFS_ENS** (~15-day) and **EC_46** (46-day, ~1-day publish lag);
    one series per (zone × model), codes `KP.LOADFC.<zone>.<MODEL>`, `sub_group` = the `loadType`
    (`demand`; `residual_demand` is a one-line add). Like `kpler_temps_forecast` it has a
    **vintage** dimension, so it lands in **`forecast_covariate`** keyed `(series_id, made_on,
    ts)`, validated by `forecast_covariate_demand_schema` (`unique(made_on, date, series_id)`).
    **Self-managing & backfills** the keep-set (no history floor — EC_46 covers the trailing
    year) + a refresh overlap; same **retention** (last 15 days + every Monday for a year) via
    the `load` hook and `etl prune kpler_power_demand_forecast`. Required params the temperature
    variant doesn't need are `loadType=demand` and `models`; `run=00z` keeps the clean single
    daily run. Unlike `kpler_generation_forecast`, **`zones` batches every area in one request**
    (plain country codes — `DE`, not `DE-LU`), so fetch issues **one request per run date**,
    **fanned out concurrently** (bounded by `_CONCURRENCY`). Zones in
    `settings/kpler_power_demand_forecast.yaml`. (ADR 0009.)
  - **kpler_power_demand_long_term** (Kpler) — hourly forward-looking electricity **demand**
    (total system load, MW) **climatology** per power zone, a covariate, from
    `…/power/loads/forecasts/long-term` — the demand analogue of `kpler_long_term_temperatures`.
    Two flavours via `baseWeatherModel`: **MEAN** (the normal) and **REF_YYYY** weather years
    (last 10 completed years, recomputed each run). `zones[]` batches every area in one request, so
    a run is `len(models)` (= 11) requests, **fanned out concurrently** (bounded by
    `_CONCURRENCY`). 11 series per area, codes `KP.LOADLT.<zone>.<MODEL>`, `sub_group` = `demand`
    (lines up with the actual `KP.LOAD.<zone>` and forecast `KP.LOADFC.<zone>.<MODEL>`). Unlike the
    short-term `/power/loads/forecasts`, this endpoint takes **no `loadType`** (total demand only)
    and `zones` is the **country-code** enum — Germany is `DE`, not `DE-LU` (both verified live).
    HTTP Basic Auth (shared Kpler key), JSON; **full refresh weekly** of the forward window
    `[today, +24 months]`. Like the long-term generation (and unlike the long-term temperatures)
    this profile is **not** run-date-independent (MEAN shifts by hundreds of MW across run dates),
    so we deliberately omit `runDate` to take the **latest** run and overwrite the **single-vintage
    `covariate`** in place — we do not vintage it (that's `kpler_power_demand_forecast`). Validated
    by the shared `demand_schema` (MW band). Zones in
    `settings/kpler_power_demand_long_term.yaml`. (ADR 0008.)
  - **kpler_power_forward_curve** (Kpler) — daily power-price **forward curve** (EUR/MWh) per
    power zone, a forecast **covariate** for gas-for-power demand (forward power prices drive gas
    dispatch), from `…/power/prices/price-forward-curve/power` — the EEX-futures-settlement curve
    of a given **trading date**. One baseload series per zone, code `KP.PFC.<zone>`, `sub_group` =
    the `demandPeriod` (`base`), `main` scenario. **`zones` batches every area in one request**, so
    a run is **one request per trading date**, **fanned out concurrently** (bounded by
    `_CONCURRENCY`). `zones` is the **bidding-zone** enum — Germany is `DE-LU` (DK and LT aren't in
    it, so they're dropped); the endpoint **rejects a `timezone` param** (HTTP 422), unlike the
    loads/generations forecasts. HTTP Basic Auth (shared Kpler key), JSON; **self-managing &
    backfills** the forecast keep-set of trading dates (+ a 3-day refresh). The **vintage** is
    `tradingDate`, so rows land in the vintage-keyed `forecast_covariate` (keep all of the last 15
    days + every Monday for a year; history begins ~2023, weekend/holiday dates have no
    settlement). Validated by `forecast_covariate_power_price_schema` (EUR/MWh band). Zones in
    `settings/kpler_power_forward_curve.yaml`. (ADR 0009.)
  - **kpler_power_spot** (Kpler) — hourly actual **day-ahead electricity spot price**
    (EUR/MWh) per power zone, an exogenous **covariate** for gas-for-power demand (high power
    prices → gas plants are in the money and run), from `…/power/prices/day-ahead`. One series
    per area, code `KP.SPOT.<zone>`, `sub_group` = `day_ahead`. HTTP Basic Auth (shared Kpler
    key), JSON; **incremental** (self-managed from the last loaded timestamp; first run
    backfills from 2016 — the API floors `startDate` at 2014 and 2015 is empty). Like
    `kpler_generation_actual`, **`zones` batches every area in one request**, so a run is one
    request per ~1-year date chunk, **fanned out concurrently** (bounded by `_CONCURRENCY`).
    But `zones` is the ENTSO-E **bidding-zone** enum (like `kpler_generation_forecast`, not the
    loads/country-code family): Germany is `DE-LU`, Denmark `DK1`, Italy `IT-NORTH` (Italy's
    national `IT-PUN` is in the enum but returns no data); GB is normalised to EUR (no currency
    split). Day-ahead prices legitimately go **negative** and spike high. Hourly → lands in the
    `covariate` table via the `load` hook; raw hourly UTC stored, gas-day aggregation applied
    downstream in `ml/`. Validated by `spot_price_schema` (EUR/MWh band). Zones in
    `settings/kpler_power_spot.yaml`. (ADR 0008.)
- **ml/** — the data-science core. Reads clean series from Postgres, builds features
  (covariates), fits/backtests models from a registry, tracks experiments in MLflow,
  and writes forecasts back to Postgres. Models are config-selected, not hardcoded.
- **api/** — FastAPI. Serves series and forecasts from Postgres. No business logic
  beyond shaping/serving; it never re-runs the pipeline.
- **web/** — React + Vite dashboard. Talks only to the API.
- **core/** — shared config, Postgres session, settings loader, logging, types.
  Everything else imports from here instead of re-implementing.

## Why Postgres in the middle

The store is the contract between producers (etl, ml) and consumers (api). Producers
write; the API reads. The web app never sees the pipeline, and the pipeline never
serves HTTP. Reuses the existing v1 power DB — no new infra (ADR 0002).

## Trusted data

"100% trust" is a layer, not a hope. Every connector's output passes a Pandera
schema (columns, dtypes, ranges, nullability) and freshness/duplicate checks
**before** load. Bad data fails loudly and is never written. Details and the list of
checks: [`data-contracts.md`](data-contracts.md).

## Performance — killing the ~1h run

The legacy run was slow because it re-fetched everything, retrained every model, and
looped series sequentially. v2 design targets (implementation goals, not yet built):

- **Incremental fetch** — connectors pull only the delta since last load.
- **Persisted models** — fit once, reuse; retrain on a schedule or when drift is detected.
- **Serve, don't recompute** — the API reads stored forecasts; it never triggers a run.
- **Parallelism at the series level**, not just per-scenario.
- **Decoupled stages** — ETL and forecasting run (and scale) independently via the CLI.

## Orchestration

Lightweight by choice (ADR 0001 / decisions): per-source connectors behind a CLI
(`etl run <source>`), scheduled by cron/CI. No Dagster/Prefect until lineage or
backfills actually demand it.

## Experimentation

Models live in a registry; experiments are config-driven and tracked in MLflow
(local file backend to start). Adding/comparing a model is `/add-model` + a backtest,
not a surgery on the run loop.
