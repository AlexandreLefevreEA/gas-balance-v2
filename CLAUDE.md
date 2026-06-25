# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Gas Balance v2 — a monorepo that forecasts the EU natural-gas supply/demand
balance. It replaces the legacy monolith in `legacy/` (a single ~1h batch run that
emitted Excel files) with four segregated, independently testable parts:

| Path | Role |
|---|---|
| `etl/` | Ingest each data source independently → transform → **validate** → load to Postgres |
| `ml/` | The data-science core: models, features, backtesting, experiments (MLflow) |
| `api/` | FastAPI service that serves series & forecasts from Postgres |
| `web/` | React + Vite dashboard that consumes the API |
| `core/` | Shared library: config, Postgres session, settings loader, logging, types |
| `infra/` | Local dev stack (docker-compose) + DB migrations |
| `docs/` | Architecture, data contracts, runbook, migration map, ADRs |
| `legacy/` | Frozen v1, **local reference only — excluded from VCS** (see `legacy/CLAUDE.md`) |

> Status: **scaffold**. Folders, docs and manifests exist; source is added per
> subsystem. Read the subsystem's own `CLAUDE.md` before working inside it.

## Architecture in one line

```
sources → [etl: fetch → transform → validate → load] → Postgres → [api] → [web]
                                                          ▲
                                  [ml: fit / backtest / forecast] writes forecasts ┘
```

Full picture: `docs/architecture.md`. What "trusted data" means: `docs/data-contracts.md`.

## Commands

Single entrypoint is the `Makefile` (targets activate as subsystems are built):

| Command | Does |
|---|---|
| `make setup` | uv env + workspace install; `npm install` for web |
| `make lint` | ruff check + ruff format --check + mypy (python); eslint + tsc (web) |
| `make test` | pytest (python); vitest (web) |
| `make run-etl` | run the ETL CLI |
| `make run-api` | run the API locally |
| `make dev` | docker-compose: postgres + api + web |

Python is a **uv workspace** (`core`, `etl`, `ml`, `api`). Single test:
`uv run pytest etl/tests/test_x.py::test_y`. Web lives in `web/` (npm).

> Windows/PowerShell note: `make` needs WSL or Git-Bash. Without it, run the
> underlying `uv …` / `npm …` commands directly (each `make` target is a thin wrapper).

> **Before pushing, run the full check** — `make lint` **and** `make test`; CI runs
> both and a red check blocks the merge. Without `make`, run *every* underlying command,
> not a subset: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`,
> `uv run pytest` (+ the `web/` equivalents). Note `ruff check` (lint) and
> `ruff format --check` (formatting) are **separate** — passing one does not pass the other.

> **Wait for review before pushing.** Commit locally and present the diff/summary; do
> **not** `git push` until the user (or a reviewer) has explicitly approved. A green full
> check is necessary but not sufficient — human review gates the push.

## Conventions

- Python 3.12, type hints everywhere; `ruff` + `mypy --strict` must pass.
- Every subsystem owns its `tests/`; any data/IO code gets a contract test.
- Each data source is a self-contained connector behind ONE interface — see
  `etl/src/gasbalance_etl/connectors/CLAUDE.md`. Sources are **not chosen yet**;
  the scaffold ships the contract + a template only.
- Config & secrets come from env (`.env`, never committed); see `.env.example` for names.
- Architectural decisions live in `docs/adr/`. Add one with `/new-adr`.
- **Never touch `legacy/`** — frozen reference, excluded from VCS, reads blocked in
  `.claude/settings.json`. Don't edit, import, run, or depend on it; port what you
  need into the v2 subsystems. (One-time config extraction is the only exception.)
- The DB schema name comes from `DB_SCHEMA` (config) — never hardcode it, including in migrations.
- v2 does **not** depend on the legacy internal packages (`ea-power-timeseries`,
  `ea-connections`) for now — connectors talk to sources directly.
- Be lazy in the good sense: reuse `core/`, prefer stdlib/native, smallest change
  that works. Do **not** add Nx/dbt/DVC/Dagster/an orchestrator until a real need
  appears (the "not now" list is in `docs/adr/0005-lean-ds-tooling.md`).

## Security

`.env` and `legacy/` are git-ignored; secrets stay out of VCS.
