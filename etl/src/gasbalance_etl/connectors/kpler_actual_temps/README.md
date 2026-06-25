# kpler_actual_temps — Kpler actual temperatures

Hourly population-weighted 2 m temperature (°C) per power zone, used as a demand
covariate. Kpler has **no observed/reanalysis** product, so "actual" = the **day-ahead
(D-1) slice** of each archived **00z EC_OP** run: the run on day D forecasts forward,
and its slice for day D+1 is the 1-day-ahead value — a full 24 h, consistent back to
~2018. (The same-day D-0 slice is only ~2 h before late-2025, so D-1 is the one
methodology that spans the whole archive. See ADR 0008.)

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `KPLER_API_KEY_V2` (a base64 `id:secret`, sent verbatim; `+ KPLER_BASE_URL`, default `https://api.kpler.com/v2`). |
| **Format** | JSON (`{"data": [...]}`). |
| **Refresh** | **Incremental & self-managing** — `fetch()` reads the last loaded `covariate` timestamp and pulls only newer delivery days (minus a 5-day refresh overlap). First run backfills from `_HISTORY_START` (~3 000 requests, a few minutes). |
| **Cadence** | Daily (cron — the 00z run's day-ahead lands by mid-morning UTC). |
| **Series** | One per balance area (`area` ↔ Kpler `zone`, identity map) in `../../settings/kpler_actual_temps.yaml`. |
| **Coverage** | Per-zone history varies — major Western-EU zones from ~2018; smaller/eastern zones start later. Absent zones are skipped, not errored. |
| **Units** | °C (`degC`). |
| **Stored in** | `covariate` (hourly `ts`), **not** `observation` (daily) — see ADR 0008. |

## Endpoint

`GET power/loads/forecasts/temperature?runDate=<D>&run=00z&zones=<all>&models=EC_OP&granularity=hourly&timezone=UTC`
→ `{"data": [{startDate, zone, model, run, value, …}]}`, ~9–15 forecast days from that
run. The `zones[]` param returns **all areas in one request**, so the backfill is one
request per run-day (not per zone). We keep only the day-ahead slice (`startDate`'s day
== `runDate` + 1).

## Shape

`fetch(since)` async-fetches one request per run-day (concurrency-bounded) → a tidy long
frame `[zone, date(ts), value]`. `to_canonical(raw)` merges with the dictionary on `zone`
to attach `series_id`/`area`/…, producing canonical rows validated by
`../../validation/temperature.py` (plausible-range guard). `load` routes them to
`covariate` keyed by the full hourly timestamp.

## Why D-1 (and not D-0)

Kpler's archived historical runs only cover from ~22:00 of the run day, so the same-day
(D-0) slice has ~2 h before late-2025 — useless for a daily series. The next-day (D-1)
slice is a clean 24 h for every run back to ~2018, giving one consistent definition of
"actual" across the whole history. For true reanalysis actuals before 2018 (to match the
CE gas history to 2014), source ERA5 in a separate connector.

## Adding / changing areas

Add a line to `kpler_actual_temps.yaml` with a `code`, `area` (balance area), and `zone`
(Kpler zone). Today every area maps 1:1 to the identically-named zone. To aggregate a
split country later (e.g. Italy's bidding zones), change the connector to average several
zones into one area.

## Test

`etl/tests/test_kpler_actual_temps.py` — fixture-based, no live network. Covers the
day-ahead slice extraction (date filtering + null drop), the zone→canonical mapping, and
the temperature-range gate (an absurd value is blocked).
