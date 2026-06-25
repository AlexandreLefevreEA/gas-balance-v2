# etl/ — ingestion (`gasbalance_etl`)

Turn external data into **trusted** rows in Postgres. The pipeline per source is
always the same four steps:

```
fetch → transform → VALIDATE → load
```

## Layout

- `connectors/` — one self-contained subpackage **per source**. The contract and the
  copy-to-start template live here; see `connectors/CLAUDE.md`. **No sources are
  built yet** (ADR 0003) — add one with `/add-connector`.
- `transforms/` — shared mapping helpers (resampling, derived series, fillna policy).
- `validation/` — Pandera schemas + cross-cutting checks = the data-trust layer.
- `load/` — write validated canonical series to Postgres (idempotent upserts).
- `cli.py` — `etl run <source>` / `etl run all`. The only entrypoint.
- `settings/` — hierarchical YAML series/country/region config (ported from legacy).

## Rules

- A source is **never** loaded unless its Pandera schema passes (`docs/data-contracts.md`).
- Connectors are independent — one failing or being swapped never blocks the others.
- Incremental by default: fetch only the delta since the last load.
- Reuse `core/` for config/db/logging. Tests use recorded fixtures, never live network.

> Scaffold: source dirs are empty pending implementation.
