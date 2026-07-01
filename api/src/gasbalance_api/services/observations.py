"""Daily-actuals reads (batched by code)."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from gasbalance_api.schemas.common import Point, SeriesPoints
from gasbalance_api.services._util import resolve_ids, split_codes
from gasbalance_core.models import Observation


def read_observations(
    session: Session, codes_raw: str, frm: dt.date | None, to: dt.date | None
) -> list[SeriesPoints]:
    ids = resolve_ids(session, split_codes(codes_raw))
    if not ids:
        return []
    stmt = select(Observation.series_id, Observation.obs_date, Observation.value).where(
        Observation.series_id.in_(ids)
    )
    if frm is not None:
        stmt = stmt.where(Observation.obs_date >= frm)
    if to is not None:
        stmt = stmt.where(Observation.obs_date <= to)
    stmt = stmt.order_by(Observation.series_id, Observation.obs_date)

    grouped: dict[int, list[Point]] = defaultdict(list)
    for sid, day, value in session.execute(stmt):
        grouped[int(sid)].append(Point(date=day, value=float(value)))
    return [SeriesPoints(code=ids[sid], points=pts) for sid, pts in grouped.items()]
