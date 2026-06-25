"""Kpler connector config — connection/secret NAMES read from env (prefix `KPLER_`).

Lives here, not in `core.Settings`, so core stays source-agnostic. Local dev reads
the repo-root `.env`; prod injects real env vars.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .../etl/src/gasbalance_etl/connectors/kpler_actual_temps/config.py -> parents[5] = repo root.
_REPO_ROOT = Path(__file__).resolve().parents[5]


class KplerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KPLER_",
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    api_key_v2: str  # KPLER_API_KEY_V2 — base64 'id:secret', sent verbatim as HTTP Basic auth
    base_url: str = "https://api.kpler.com/v2"  # KPLER_BASE_URL


@lru_cache
def get_kpler_settings() -> KplerSettings:
    return KplerSettings()  # type: ignore[call-arg]  # values come from env/.env
