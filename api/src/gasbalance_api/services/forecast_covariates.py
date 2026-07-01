"""Forecast-covariate (driver forecast) reads — the latest run, then a daily mean.

Like `covariates.py`, but reads `forecast_covariate` (kept per `made_on` vintage) and returns
only the latest forecast run per series (its most recent `made_on`), so the chart shows that
run's forward projection — not a per-hour backfill of every past vintage (which would hug the
actual across all history). Same UTC day boundary as `covariates.py` / ml's `_hourly_to_daily`,
so a covariate's actual and forecast line up on the same daily grain.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gasbalance_api.schemas.common import Point, SeriesPoints
from gasbalance_api.services._util import resolve_ids, split_codes
from gasbalance_core.models import ForecastCovariate


def read_forecast_covariates(
    session: Session, codes_raw: str, frm: dt.date | None, to: dt.date | None
) -> list[SeriesPoints]:
    ids = resolve_ids(session, split_codes(codes_raw))
    if not ids:
        return []
    fc = ForecastCovariate
    # The latest forecast run per series (max made_on); return only that run's hours.
    # ponytail: latest run only; add an optional `made_on` cap for historical-vintage views.
    latest_run = (
        select(fc.series_id, func.max(fc.made_on).label("mo"))
        .where(fc.series_id.in_(ids))
        .group_by(fc.series_id)
        .subquery()
    )
    day = func.date_trunc("day", func.timezone("UTC", fc.ts))
    stmt = select(fc.series_id, day.label("d"), func.avg(fc.value)).join(
        latest_run,
        (fc.series_id == latest_run.c.series_id) & (fc.made_on == latest_run.c.mo),
    )
    if frm is not None:
        stmt = stmt.where(day >= frm)
    if to is not None:
        stmt = stmt.where(day <= to)
    stmt = stmt.group_by(fc.series_id, day).order_by(fc.series_id, day)

    grouped: dict[int, list[Point]] = defaultdict(list)
    for sid, d, value in session.execute(stmt):
        grouped[int(sid)].append(Point(date=d.date(), value=float(value)))
    return [SeriesPoints(code=ids[sid], points=pts) for sid, pts in grouped.items()]
