# gasbalance-etl

Source-agnostic ETL: each data source is an independent connector that fetches raw
data, maps it to the canonical series schema, is validated (Pandera) before load, and
upserts into Postgres. Run all sources or one in isolation via the `etl` CLI.

Add a source with `/add-connector`. Contract: `src/gasbalance_etl/connectors/CLAUDE.md`.
See also [`CLAUDE.md`](CLAUDE.md) and `../docs/data-contracts.md`.
