# ce — Commodity Essentials

European gas fundamentals (flows, storage, demand, supply). Pulled from the CE API,
ported one-time from the legacy `CEtools.py` / `raw.py`.

| | |
|---|---|
| **Auth** | HTTP **Basic Auth** — `CE_USERNAME` / `CE_PASSWORD` (+ `CE_BASE_URL`, default `https://commodityessentials.com/api/`). |
| **Format** | CSV (`Accept: text/csv`). |
| **Refresh** | **Full** — re-fetch the whole history (since 2014-01-01) every run; `since` is ignored. Idempotent upsert makes re-runs safe. |
| **Cadence** | Hourly (cron — see `docs/runbook.md`). |
| **Series** | 223 series in `../../settings/ce.yaml` (ported from legacy `eu_balance.yaml`), composed from ~258 raw CE ids. |
| **Units** | mcm (a few `%`). |

## Endpoints

- **Catalog:** `GET eugasmeta?id=series` → all series ids + metadata (columns:
  `seriesId, seriesName, variable, source, …`). Look up ids here.
- **Series:** `GET eugasseries?id=<ids>&dateFrom=2014-01-01&dateTo=<today>&unit=mcm`
  → CSV `DateExcel, Date, <col per id>`. **`id` takes comma-separated ids**, so the raw
  series are fetched in a few batched requests, run async (`httpx.AsyncClient`).

Fastest backfill = batched multi-id `eugasseries` (no date-window cap). The `…bulk`
endpoints are 14-day-capped → incremental only, not since-2014.

## Shape

`fetch(since)` async-fetches the raw ids in `_BATCH_IDS`-sized batches → a wide frame
(index=date, cols=ce_id). `to_canonical(raw)` composes each `ce.yaml` entry as
`sum(positive) − sum(negative)` (skipna=False) with optional `fillna`/`skip_last_day`,
into the canonical schema, validated by `../../validation/ce.py`.

## Adding / regenerating series

`ce.yaml` was generated from the legacy per-country settings. Add a line with a `code`
(ours) and `positive`/`negative` raw CE ids (from the catalog). Removing a line stops new
writes but leaves that series' history intact (load never deletes). DerivedData
(cross-column balances) and the `level_pct` ratio columns are intentionally **not** here —
those belong in a later transform / `ml/features`.

## Test

`etl/tests/test_ce.py` — fixture-based, no live network. Covers the CSV parser, the
canonical mapping, and that a non-finite value is blocked by the schema.
