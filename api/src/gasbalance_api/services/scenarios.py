"""Scenario reads."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from gasbalance_api.schemas.scenarios import ScenarioOut
from gasbalance_core.models import Scenario


def list_scenarios(
    session: Session, *, kind: str | None = None, active: bool | None = True
) -> list[ScenarioOut]:
    stmt = select(Scenario)
    if kind is not None:
        stmt = stmt.where(Scenario.kind == kind)
    if active is not None:
        stmt = stmt.where(Scenario.is_active.is_(active))
    rows = session.execute(stmt.order_by(Scenario.code)).scalars().all()
    return [ScenarioOut.model_validate(r) for r in rows]
