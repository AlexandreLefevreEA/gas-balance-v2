"""Environment-driven settings. One knob — DATABASE_URL — selects the env.

Local dev reads the repo-root .env (git-ignored). Prod injects real env vars and
has no .env, so prod credentials never live on a dev machine. See docs/adr/0002 and
the env-migration strategy.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# core/src/gasbalance_core/config.py -> parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "local"  # local | prod  — guards destructive ops
    database_url: str  # required; fail loudly if missing
    db_schema: str = "gas_balance"  # all objects live in this schema

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from env/.env
