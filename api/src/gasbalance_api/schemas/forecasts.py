"""Forecast response shapes (grouped by series + scenario)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class ForecastPoint(BaseModel):
    target_date: dt.date
    value: float
    model_run_id: str
    made_on: dt.date | None = None  # populated only for made_on=<date>|all


class ForecastSeries(BaseModel):
    code: str
    scenario: str
    points: list[ForecastPoint]
