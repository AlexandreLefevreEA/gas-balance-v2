# Energy Quantified (Montel EQ) API — reference

Source notes for the **Energy Quantified** REST API (the platform is now branded **Montel EQ**),
captured while building the `eq_coal_spot` connector so future EQ connectors (other commodity
curves, power/weather series) can skip the doc-spelunking. Interactive docs (Swagger UI, JS-rendered
so not capturable as static HTML): <https://app.energyquantified.com/api/docs/>. Python client docs:
<https://energyquantified-python.readthedocs.io/>.

## Connection & auth

| | |
|---|---|
| **Base URL** | `https://app.energyquantified.com/api` |
| **Auth** | Header **`X-API-Key: <key>`** (per-account key; *not* HTTP Basic). |
| **Accept** | `application/json` |

```bash
curl -X GET \
  "https://app.energyquantified.com/api/ohlc/Futures%20Coal%20API-2%20USD%2Ft%20ICE%20OHLC/latest/?date=2025-12-05" \
  -H "accept: application/json" -H "X-API-Key: <key>"
```

## OHLC endpoints

Futures/forward curves are exposed as **OHLC** series. A curve is addressed by its full name in the
path; curve names contain spaces and `/`, which **must be percent-encoded** — space → `%20`, `/` →
`%2F` (so a `/` in e.g. "USD/t" stays part of the name, not a path separator).

| Endpoint | Returns |
|---|---|
| `GET /ohlc/{curve}/latest/?date=<D>` | The full contract list (every `front` and `period`) for the most recent trading day **≤ D**. One trading day's snapshot — **not** a time series. No `period`/`front`/`delivery` filtering. |
| `GET /ohlc/{curve}/?from=<D>&to=<D>` (load) | OHLC entries across a date range; supports filtering. (Behind the Python client's `load`.) |

The Python client also offers `eq.ohlc.load_front_as_timeseries(curve, begin, end, period, front,
field)` — a **continuous front contract as a daily time series** (e.g. rolling front-month
settlement) in one call. This is the natural primitive for a rolling-front-month series; we did not
use it in `eq_coal_spot` only because its exact REST path/params weren't confirmed against a live key.

### OHLC entry fields

Each entry in the response list:

| Field | Meaning |
|---|---|
| `traded` | The trading date (use this as the observation date). |
| `period` | Delivery period type: `DAY` / `WEEK` / `MONTH` / `QUARTER` / `SEASON` / `YEAR`. |
| `front` | Contract proximity: `1` = front contract, `2` = second front, … |
| `delivery` | The contract's delivery date. |
| `open`, `high`, `low`, `close` | Trade prices for the day (any may be unset). |
| `settlement` | Settlement (end-of-day reference) price — may be unset intraday. |
| `volume`, `open_interest` | Traded volume / open interest (any may be unset). |

The list is wrapped in a JSON object (the OHLC list under `data`, with curve metadata alongside);
some endpoints return a bare list. Parse tolerantly.

**Rolling front month** = the entry with `period == "MONTH"` and `front == 1`. The daily reference
price is `settlement`; fall back to `close` when settlement isn't published yet.

> `/latest/?date=X` returning the latest trading day **≤ X** means: key the observation on the
> response's `traded` date (not the requested `X`), and dedupe — consecutive requests around a
> holiday repeat the prior trading day.

## Curves used here

| Connector | Curve name | Unit | Series |
|---|---|---|---|
| `eq_coal_spot` | `Futures Coal API-2 USD/t ICE OHLC` | USD/t | `EQ.COAL.API2` (ICE API-2, CIF ARA European coal benchmark) |
