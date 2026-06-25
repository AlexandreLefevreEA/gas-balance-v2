"""Kpler connector config — connection/secret NAMES read from env (prefix `KPLER_`).

Lives here, not in `core.Settings`, so core stays source-agnostic. Local dev reads
the repo-root `.env`; prod injects real env vars.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from gasbalance_core.config import DOTENV


class KplerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KPLER_",
        env_file=DOTENV,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    api_key_v2: str  # KPLER_API_KEY_V2 — base64 'id:secret', sent verbatim as HTTP Basic auth
    base_url: str = "https://api.kpler.com/v2"  # KPLER_BASE_URL


@lru_cache
def get_kpler_settings() -> KplerSettings:
    return KplerSettings()  # type: ignore[call-arg]  # values come from env/.env
