"""Energy Quantified (Montel EQ) connector config — secret NAMES from env (prefix `EQ_`).

Lives here, not in `core.Settings`, so core stays source-agnostic. Local dev reads the
repo-root `.env`; prod injects real env vars. Auth is a header (`X-API-Key`), not Basic.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from gasbalance_core.config import DOTENV


class EqSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EQ_",
        env_file=DOTENV,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: str  # EQ_API_KEY — sent verbatim as the `X-API-Key` request header
    base_url: str = "https://app.energyquantified.com/api"  # EQ_BASE_URL


@lru_cache
def get_eq_settings() -> EqSettings:
    return EqSettings()  # type: ignore[call-arg]  # values come from env/.env
