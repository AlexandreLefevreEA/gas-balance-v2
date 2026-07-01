"""Scenario reads. (Phase B will add authoring/run/status here.)"""

from __future__ import annotations

from fastapi import APIRouter

from gasbalance_api.dependencies import DbDep
from gasbalance_api.schemas.scenarios import ScenarioOut
from gasbalance_api.services.scenarios import list_scenarios

router = APIRouter(tags=["scenarios"])


@router.get("/scenarios")
def get_scenarios(
    db: DbDep, kind: str | None = None, active: bool | None = True
) -> list[ScenarioOut]:
    return list_scenarios(db, kind=kind, active=active)
