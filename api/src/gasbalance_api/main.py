"""FastAPI app — a thin read layer over Postgres (see api/CLAUDE.md)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gasbalance_api.config import get_api_settings
from gasbalance_api.routers import forecasts, scenarios, series

app = FastAPI(title="Gas Balance API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_api_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(series.router)
app.include_router(forecasts.router)
app.include_router(scenarios.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
