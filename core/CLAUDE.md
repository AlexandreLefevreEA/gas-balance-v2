# core/ — shared library (`gasbalance_core`)

The one place for things every other subsystem needs. **Import from here; don't
re-implement config, DB sessions, or logging in etl/ml/api.**

## What lives here

- `config.py` — typed settings loaded from env (pydantic-settings). Single source of config.
- `db.py` — Postgres engine/session factory (SQLAlchemy 2.0).
- `settings/` loader — reads the hierarchical YAML series/country/region config.
- `logging.py` — structured logging setup.
- `types.py` — shared domain types (the canonical series record, enums for group/area).

## Rules

- No business logic, no models, no HTTP. This is plumbing only.
- If two subsystems need the same helper, it belongs here.
- Everything is typed; `mypy --strict` clean.

> Scaffold: `src/gasbalance_core/` is empty pending implementation. Add
> `__init__.py` + modules and declare deps in `pyproject.toml`.
