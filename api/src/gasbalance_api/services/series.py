"""Series-catalog reads."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from gasbalance_api.schemas.series import SeriesOut
from gasbalance_core.models import Series


def list_series(
    session: Session,
    *,
    area: str | None = None,
    category: str | None = None,
    active: bool | None = True,
) -> list[SeriesOut]:
    stmt = select(Series)
    if area is not None:
        stmt = stmt.where(Series.area == area)
    if category is not None:
        stmt = stmt.where(Series.category == category)
    if active is not None:
        stmt = stmt.where(Series.is_active.is_(active))
    rows = session.execute(stmt.order_by(Series.code)).scalars().all()
    return [SeriesOut.model_validate(r) for r in rows]
