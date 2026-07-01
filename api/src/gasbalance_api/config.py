"""API settings — reuses core's `.env`; adds the browser CORS origins."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from gasbalance_core.config import DOTENV


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DOTENV, env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Comma-separated browser origins allowed by CORS. Configured via API_CORS_ORIGINS
    # in .env (see .env.example for the dev value); empty = no cross-origin access.
    api_cors_origins: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
