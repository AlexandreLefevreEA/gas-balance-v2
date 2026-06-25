# api/ — FastAPI service (`gasbalance_api`)

Serves series and forecasts **from Postgres** to the web app. Thin: it shapes and
serves data, it never re-runs the pipeline or holds business logic.

## Layout (domain-folder style)

```
src/gasbalance_api/
├── main.py          # FastAPI app, include routers
├── config.py        # settings (reuses core.config)
├── dependencies.py  # get_db(), shared deps
├── routers/         # one module per domain (series, forecasts, scenarios)
├── schemas/         # Pydantic v2 request/response models
└── services/        # read logic against Postgres (via core.db)
```

## Rules

- Pydantic **v2** at the boundary (`model_validate` / `model_dump`, `ConfigDict`).
- Imports flow one way: `router → service → core.db`. No circular imports.
- Read-only by default; the API does not write the system of record.
- Reuse `core/` for config + DB session. Tests use a throwaway Postgres (testcontainers).

> Scaffold: `src/gasbalance_api/` is empty pending implementation.
