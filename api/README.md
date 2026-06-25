# gasbalance-api

FastAPI service that serves gas-balance series and forecasts from Postgres to the web
app. Thin and read-only: domain routers → services → `core.db`, Pydantic v2 at the
edge. See [`CLAUDE.md`](CLAUDE.md).

Run locally: `make run-api` (or `uv run uvicorn gasbalance_api.main:app --reload`).
