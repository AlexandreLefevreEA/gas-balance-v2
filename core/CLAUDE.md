# core/ — shared library (`gasbalance_core`)

The one place for things every other subsystem needs. **Import from here; don't
re-implement config, DB sessions, or logging in etl/ml/api.**

## What lives here

- `config.py` — `Settings` from env (pydantic-settings): `APP_ENV`, `DATABASE_URL`,
  `DB_SCHEMA`. `get_settings()` is the one entrypoint. Loads repo-root `.env` for local.
- `db.py` — SQLAlchemy 2.0 `engine`, `SessionLocal`, and `Base` (metadata bound to
  `DB_SCHEMA`; every connection sets `search_path`).
- `models.py` — the ORM tables = the DB-shape data contract (series, scenario,
  observation, forecast, etl_run, forecast_run). Producers write, the api reads.
- _later_: settings (YAML) loader, structured logging, shared types.

DB migrations live in `infra/db/` (Alembic, wired to this config). From the repo root:
`uv run alembic -c infra/db/alembic.ini upgrade head`.

## Rules

- Config/DB plumbing only — no source connectors, no models math, no HTTP.
- If two subsystems need the same helper, it belongs here.
- Everything is typed; `mypy --strict` clean.
- Schema changes go through an Alembic migration that mirrors `models.py` — never edit the DB by hand.
- The schema name is config (`DB_SCHEMA`), never a literal — migrations read it from `get_settings()`.
