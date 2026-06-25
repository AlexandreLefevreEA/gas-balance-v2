---
description: Scaffold a new ETL data-source connector from the template
argument-hint: <source-name>
---

Add a new ETL connector named `$ARGUMENTS`.

Follow the connector contract in `etl/src/gasbalance_etl/connectors/CLAUDE.md`:

1. Copy `etl/src/gasbalance_etl/connectors/_template/` to `.../connectors/$ARGUMENTS/`.
2. Implement the connector interface: `fetch()` (pull raw) and `to_canonical()` (map to the shared series schema).
3. Add a Pandera schema for its canonical output in `etl/src/gasbalance_etl/validation/`.
4. Register the connector so `etl run $ARGUMENTS` works via the CLI.
5. Add the source's secret NAMES to `.env.example` (names only, never values).
6. Write a contract test in `etl/tests/` against a recorded/fixture response (no live network in tests).
7. Document the source in `docs/architecture.md`.

Keep it minimal: reuse `core/` for config/db/logging. Don't add a dependency for what a few lines do.
