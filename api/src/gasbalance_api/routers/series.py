"""Series catalog + the daily series-data reads (observations, covariates)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from gasbalance_api.dependencies import DbDep
from gasbalance_api.schemas.common import SeriesPoints
from gasbalance_api.schemas.series import SeriesOut
from gasbalance_api.services.covariates import read_covariates
from gasbalance_api.services.forecast_covariates import read_forecast_covariates
from gasbalance_api.services.observations import read_observations
from gasbalance_api.services.series import list_series

router = APIRouter(tags=["series"])

FromQ = Annotated[dt.date | None, Query(alias="from")]


@router.get("/series")
def get_series(
    db: DbDep,
    area: str | None = None,
    category: str | None = None,
    active: bool | None = True,
) -> list[SeriesOut]:
    return list_series(db, area=area, category=category, active=active)


@router.get("/observations")
def get_observations(
    codes: str, db: DbDep, frm: FromQ = None, to: dt.date | None = None
) -> list[SeriesPoints]:
    return read_observations(db, codes, frm, to)


@router.get("/covariates")
def get_covariates(
    codes: str, db: DbDep, frm: FromQ = None, to: dt.date | None = None
) -> list[SeriesPoints]:
    return read_covariates(db, codes, frm, to)


@router.get("/forecast-covariates")
def get_forecast_covariates(
    codes: str, db: DbDep, frm: FromQ = None, to: dt.date | None = None
) -> list[SeriesPoints]:
    return read_forecast_covariates(db, codes, frm, to)
