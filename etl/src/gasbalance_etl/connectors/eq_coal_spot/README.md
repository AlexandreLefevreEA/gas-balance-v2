# eq_coal_spot — Energy Quantified coal API-2 front-month spot

Daily rolling **front-month** ICE **API-2** coal price (USD/t) — the European **CIF-ARA** coal
benchmark — used as an exogenous **covariate** for gas-for-power demand: the coal price sets
gas-vs-coal switching economics, so it drives how much gas-fired generation runs. A **single**
global series, code `EQ.COAL.API2`.

| | |
|---|---|
| **Auth** | Header **`X-API-Key: <EQ_API_KEY>`** (not Basic). `+ EQ_BASE_URL`, default `https://app.energyquantified.com/api`. |
| **Format** | JSON — a list of OHLC contract entries (under `data`, tolerant of a bare list). |
| **Refresh** | **Incremental & self-managing** — `fetch()` reads the last loaded `covariate` timestamp and pulls trading dates from there (minus a 5-day refresh overlap for late settlements). First run backfills from `_HISTORY_START` (2015). |
| **Cadence** | Daily (cron). |
| **Series** | **One** — `EQ.COAL.API2`, area `EU`, `group` = `price`, `sub_group` = `coal`. Hardcoded in `series_dict()`. |
| **Units** | USD/t. |
| **Stored in** | `covariate` (daily `ts` at midnight UTC), **not** `observation` — see ADR 0008. |

## Endpoint

`GET ohlc/{curve}/latest/?date=<D>` with `curve = "Futures Coal API-2 USD/t ICE OHLC"`.

The curve name carries spaces and a `/` ("USD/t") and **must be percent-encoded** — spaces → `%20`
and the `/` → `%2F` (so it stays part of the curve name, not a path separator):

```
GET https://app.energyquantified.com/api/ohlc/Futures%20Coal%20API-2%20USD%2Ft%20ICE%20OHLC/latest/?date=2025-12-05
    header: X-API-Key: <key>
```

`/latest/?date=X` returns the full contract list (every `front` and `period`) for the most recent
trading day **≤ X**. Each entry carries `traded` (the trading date), `period`
(DAY/WEEK/MONTH/QUARTER/SEASON/YEAR), `front` (1 = front contract, 2 = second, …), `delivery`, and
`open/high/low/close/settlement/volume/open_interest` (any may be unset).

**Rolling front month** = `period == "MONTH"` and `front == 1`. We take that entry's `settlement`,
falling back to `close` when settlement isn't published yet (the current day, or a thin holiday).

> Because `/latest/?date=X` returns the latest trading day **≤ X**, the canonical `date` is keyed on
> the response's own `traded` date (NOT the requested `date`). Around holidays consecutive requests
> repeat the prior trading day; `to_canonical` dedupes on `(date, series_id)`.

A run is **one GET per weekday** in the window (front-month coal doesn't trade weekends; a weekend
request just repeats Friday), **fanned out concurrently** (`httpx.AsyncClient` + `arequest`, bounded
by `_CONCURRENCY`) over the shared 429/5xx retry/backoff.

> EQ also exposes a continuous-front time series (`load_front_as_timeseries` in the Python client),
> which would return the whole rolling-front-month history in one call. We use the per-day `/latest/`
> loop instead — it's the path the request specified, matches `kpler_carbon_spot`, and steady-state
> is one request per run. Swap to the range endpoint if its REST path is confirmed and backfill cost
> matters. Full source reference: `docs/sources/energy-quantified-api.md`.

## Shape

`fetch(since)` fans out one `/latest/` request per trading date → a long frame
`[traded, period, front, settlement, close]`. `to_canonical(raw)` keeps the front month
(`period=MONTH`, `front=1`), takes `settlement` (else `close`), drops nulls, dedupes
`(date, series_id)`, and stamps the single series's canonical columns — validated by
`../../validation/coal_spot.py` (a generous USD/t band). `load` routes the rows to `covariate`.

## Test

`etl/tests/test_eq_coal_spot.py` — fixture-based, no live network. Covers the front-month filter,
the settlement→close fallback, the holiday duplicate-trading-day dedupe, single-series mapping, and
the USD/t sanity band (an absurd value is blocked).
