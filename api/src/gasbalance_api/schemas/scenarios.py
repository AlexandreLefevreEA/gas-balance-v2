"""Scenario response shape (weather replays + custom definitions/combos)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    description: str | None
    kind: str
    # JSONB: present only on authored custom *definitions*; null for weather rows + combos.
    adjustments: list[dict[str, Any]] | None
    weather_years: list[str] | None
    is_active: bool
