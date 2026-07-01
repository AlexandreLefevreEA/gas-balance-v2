"""Forecast reads — latest vintage (default), a specific vintage, or all vintages."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from gasbalance_api.schemas.forecasts import ForecastPoint, ForecastSeries
from gasbalance_api.services._util import resolve_ids, split_codes
from gasbalance_core.models import Forecast


def _csv(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _parse_made_on(made_on: str) -> tuple[str, dt.date | None]:
    if made_on in ("latest", "all"):
        return made_on, None
    try:
        return "date", dt.date.fromisoformat(made_on)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="made_on: 'latest', 'all', or an ISO date (YYYY-MM-DD)"
        ) from None


def read_forecasts(
    session: Session,
    codes_raw: str,
    *,
    scenario: str | None,
    frm: dt.date | None,
    to: dt.date | None,
    made_on: str,
    models: str | None,
) -> list[ForecastSeries]:
    ids = resolve_ids(session, split_codes(codes_raw))
    if not ids:
        return []
    mode, on_date = _parse_made_on(made_on)

    stmt = select(
        Forecast.series_id,
        Forecast.scenario,
        Forecast.target_date,
        Forecast.value,
        Forecast.model_run_id,
        Forecast.made_on,
    ).where(Forecast.series_id.in_(ids))
    if scenario:
        stmt = stmt.where(Forecast.scenario.in_(_csv(scenario)))
    if models:
        stmt = stmt.where(Forecast.model_run_id.in_(_csv(models)))
    if frm is not None:
        stmt = stmt.where(Forecast.target_date >= frm)
    if to is not None:
        stmt = stmt.where(Forecast.target_date <= to)

    include_made_on = mode != "latest"
    if mode == "latest":
        # DISTINCT ON (series, scenario, target_date) + made_on DESC = newest vintage per
        # target date (model_run_id breaks ties deterministically). ix_forecast_latest covers it.
        stmt = stmt.order_by(
            Forecast.series_id,
            Forecast.scenario,
            Forecast.target_date,
            Forecast.made_on.desc(),
            Forecast.model_run_id,
        ).distinct(Forecast.series_id, Forecast.scenario, Forecast.target_date)
    elif mode == "date":
        stmt = stmt.where(Forecast.made_on == on_date).order_by(
            Forecast.series_id, Forecast.scenario, Forecast.target_date
        )
    else:  # all vintages
        stmt = stmt.order_by(
            Forecast.series_id, Forecast.scenario, Forecast.target_date, Forecast.made_on
        )

    grouped: dict[tuple[int, str], list[ForecastPoint]] = defaultdict(list)
    for sid, scen, target, value, mrid, mon in session.execute(stmt):
        grouped[(int(sid), str(scen))].append(
            ForecastPoint(
                target_date=target,
                value=float(value),
                model_run_id=str(mrid),
                made_on=mon if include_made_on else None,
            )
        )
    return [
        ForecastSeries(code=ids[sid], scenario=scen, points=pts)
        for (sid, scen), pts in grouped.items()
    ]
