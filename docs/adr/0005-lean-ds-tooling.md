# 0005. Lean DS tooling; defer DVC/dbt/Dagster/Nx

- Status: Accepted
- Date: 2026-06-25

## Context

Web research surfaced a maximal stack (Nx, Kedro, Dagster/Prefect, dbt, DVC, lakeFS,
Snowflake). For a small DS team rebuilding a single forecasting pipeline, most of
that is speculative weight — tools to learn and maintain before there's a problem
they solve.

## Decision

Start **lean**:

- **MLflow** (local file backend) for experiment tracking + model registry.
- **Pandera** for data validation (Pydantic at the API edge).
- **Parquet** for ML artifacts; Postgres is the system of record.

Explicitly **not now**: Nx/Turborepo, Kedro, Dagster/Prefect, dbt, DVC, lakeFS,
cloud warehouse, Kubernetes.

## Consequences

- Easy: few moving parts, fast onboarding, every tool earns its place.
- Give up: turnkey data/model versioning (DVC), warehouse tests (dbt), lineage UIs (Dagster).
- Each deferred tool has its own trigger below.

## Trigger to revisit

- **DVC**: when we need reproducible dataset/model versions tied to git commits.
- **Dagster/Prefect**: see ADR 0001 (lineage, backfills, observability).
- **dbt**: if/when a warehouse is adopted (ADR 0002).
- **Nx/Turborepo**: if web grows into multiple apps/packages needing task graph caching.
