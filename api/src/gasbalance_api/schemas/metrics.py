"""Forecast-error metric shapes (per scenario + model, by horizon bucket)."""

from __future__ import annotations

from pydantic import BaseModel


class MetricBucket(BaseModel):
    bucket: str  # h1 | h2-7 | h8-30 | h31-90 | h91-365 | h366+
    n: int
    mae: float
    rmse: float
    bias: float  # mean signed error (forecast - actual)


class MetricGroup(BaseModel):
    scenario: str
    model_run_id: str
    buckets: list[MetricBucket]
