"""Forecast reads + the on-the-fly error metrics."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from gasbalance_api.dependencies import DbDep
from gasbalance_api.schemas.forecasts import ForecastSeries
from gasbalance_api.schemas.metrics import MetricGroup
from gasbalance_api.services.forecasts import read_forecasts
from gasbalance_api.services.metrics import read_metrics

router = APIRouter(tags=["forecasts"])

FromQ = Annotated[dt.date | None, Query(alias="from")]


@router.get("/forecasts")
def get_forecasts(
    codes: str,
    db: DbDep,
    scenario: str | None = None,
    frm: FromQ = None,
    to: dt.date | None = None,
    made_on: str = "latest",
    models: str | None = None,
) -> list[ForecastSeries]:
    return read_forecasts(
        db, codes, scenario=scenario, frm=frm, to=to, made_on=made_on, models=models
    )


@router.get("/metrics")
def get_metrics(
    code: str, db: DbDep, scenario: str | None = None, models: str | None = None
) -> list[MetricGroup]:
    return read_metrics(db, code, scenario=scenario, models=models)
