"""CE connector config — connection/secret NAMES read from env (prefix `CE_`).

Lives here, not in `core.Settings`, so core stays source-agnostic. Local dev reads
the repo-root `.env`; prod injects real env vars.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from gasbalance_core.config import DOTENV


class CeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CE_",
        env_file=DOTENV,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    username: str  # CE_USERNAME — used as HTTP Basic Auth user
    password: str  # CE_PASSWORD — used as HTTP Basic Auth password
    base_url: str = "https://commodityessentials.com/api/"  # CE_BASE_URL (trailing slash)


@lru_cache
def get_ce_settings() -> CeSettings:
    return CeSettings()  # type: ignore[call-arg]  # values come from env/.env
