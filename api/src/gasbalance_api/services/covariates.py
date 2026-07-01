"""Covariate (driver) reads — hourly collapsed to a daily mean in SQL.

The UTC day boundary matches ml's `_hourly_to_daily` (data.py), so scatter joins on date
line up with what the models actually consume.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gasbalance_api.schemas.common import Point, SeriesPoints
from gasbalance_api.services._util import resolve_ids, split_codes
from gasbalance_core.models import Covariate


def read_covariates(
    session: Session, codes_raw: str, frm: dt.date | None, to: dt.date | None
) -> list[SeriesPoints]:
    ids = resolve_ids(session, split_codes(codes_raw))
    if not ids:
        return []
    day = func.date_trunc("day", func.timezone("UTC", Covariate.ts))
    stmt = select(Covariate.series_id, day.label("d"), func.avg(Covariate.value)).where(
        Covariate.series_id.in_(ids)
    )
    if frm is not None:
        stmt = stmt.where(day >= frm)
    if to is not None:
        stmt = stmt.where(day <= to)
    stmt = stmt.group_by(Covariate.series_id, day).order_by(Covariate.series_id, day)

    grouped: dict[int, list[Point]] = defaultdict(list)
    for sid, d, value in session.execute(stmt):
        grouped[int(sid)].append(Point(date=d.date(), value=float(value)))
    return [SeriesPoints(code=ids[sid], points=pts) for sid, pts in grouped.items()]
