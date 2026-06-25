# connectors/ — the connector contract (key extension point)

Every data source is a self-contained subpackage here implementing **one interface**,
so sources can be added, swapped, or run in isolation. Sources are **not chosen yet**
(ADR 0003): this folder ships the contract + `_template/` only.

## The contract

A connector exposes two responsibilities:

1. `fetch(since) -> raw` — pull only the delta since the last load (incremental).
2. `to_canonical(raw) -> DataFrame` — map to the canonical series schema
   (`../validation/` + `docs/data-contracts.md`). Output **must** pass the source's
   Pandera schema; the loader rejects anything that doesn't.

It also declares its **config/secret names** (read from env via `core.config`, never
hardcoded) and registers itself with the CLI so `etl run <source>` works.

## Adding a source (`/add-connector <name>`)

1. Copy `_template/` → `<name>/`.
2. Implement `fetch()` + `to_canonical()`.
3. Add the Pandera schema in `../validation/`.
4. Register with the CLI.
5. Add secret NAMES to `.env.example`.
6. Contract test in `etl/tests/` using a recorded fixture (no live network).
7. Document the source in `docs/architecture.md`.

## Rules

- One source = one subpackage. No shared mutable state between connectors.
- No business/feature logic here — that's `ml/features/`. Connectors only fetch + map + validate.
- Keep it minimal; reuse `core/`. Don't add a dependency for what a few lines do.
