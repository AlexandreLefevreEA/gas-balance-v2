"""Shared time-series response shapes (long/tidy, grouped by series)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class Point(BaseModel):
    date: dt.date
    value: float


class SeriesPoints(BaseModel):
    code: str
    points: list[Point]
