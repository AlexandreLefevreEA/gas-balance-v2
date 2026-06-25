# 0002. Reuse PostgreSQL as the trusted store

- Status: Accepted
- Date: 2026-06-25

## Context

v2 needs a store that is the contract between producers (etl, ml) and consumers
(api, web). v1 already runs PostgreSQL for power timeseries. Options considered:
DuckDB+Parquet (file-based, great for DS, weaker concurrent writes) and a cloud
warehouse (Snowflake/BigQuery — scales, adds cost/ops).

## Decision

Reuse **PostgreSQL** as the single trusted store. ETL writes validated series;
ml writes forecasts; the API reads. No new infrastructure.

## Consequences

- Easy: one well-understood store, transactional writes, the API serves directly from it.
- Easy: local dev via docker-compose Postgres.
- Give up: Parquet's zero-infra file portability; warehouse-scale analytics.
- We use Parquet only for ML artifacts/experiment outputs, not as the system of record.

## Trigger to revisit

Move to DuckDB+Parquet if the store becomes read-mostly analytical and infra-free
matters more; move to a warehouse if data volume/concurrency outgrows a single Postgres.
