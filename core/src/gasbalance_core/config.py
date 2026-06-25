"""Environment-driven settings. One knob — DATABASE_URL — selects the env.

Local dev reads the repo-root .env (git-ignored). Prod injects real env vars and
has no .env, so prod credentials never live on a dev machine. See docs/adr/0002 and
the env-migration strategy.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_dotenv() -> Path:
    """Locate the repo-root `.env` by walking up to the workspace marker (uv.lock/.git).

    Robust to file moves — no hard-coded parent depth. Falls back to the known layout if
    no marker is found (e.g. a prod image without .git), where `.env` is simply absent and
    pydantic reads real env vars instead. Connectors import `DOTENV` instead of recomputing.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "uv.lock").exists() or (parent / ".git").exists():
            return parent / ".env"
    return here.parents[3] / ".env"  # core/src/gasbalance_core/config.py -> repo root


DOTENV = _repo_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DOTENV,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "local"  # local | prod  — guards destructive ops
    database_url: str  # required; fail loudly if missing
    db_schema: str = "gas_balance"  # all objects live in this schema


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from env/.env
