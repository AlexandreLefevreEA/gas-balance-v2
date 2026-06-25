# 0001. Monorepo with a uv workspace; lightweight ETL CLI

- Status: Accepted
- Date: 2026-06-25

## Context

v1 was a single Python package doing ingest + forecast + export. v2 separates
concerns into etl / ml / api / web + a shared core, but they share types, config and
the Postgres contract. We want one place to version, lint, and test, without a heavy
build system. The team is small and primarily Python.

## Decision

One repo. Python parts (`core`, `etl`, `ml`, `api`) are members of a single **uv
workspace**; `web` is a sibling npm project. Orchestration of the pipeline is a
**lightweight CLI + cron/CI**, not Dagster/Prefect.

## Consequences

- Easy: shared tool config, atomic cross-cutting changes, one CI.
- Easy: each Python part is independently runnable/testable.
- Give up: framework-level lineage, retries dashboards, backfill UIs (we accept this for now).
- Note: `make` needs WSL/Git-Bash on Windows; raw `uv`/`npm` commands are the fallback.

## Trigger to revisit

Adopt an orchestrator (likely Dagster) when we need cross-job lineage, scheduled
backfills across many sources, or shared observability that cron can't give.
