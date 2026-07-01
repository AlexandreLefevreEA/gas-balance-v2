# ruff: noqa: E402
"""Throwaway-Postgres fixtures for the API tests.

The container URL must be in the environment BEFORE `gasbalance_core.db` is imported — core
builds its engine from lru-cached settings at import time — so the container starts and env is
set at module top, then the app is imported (hence the deliberately-late imports / E402 waiver).
Tables come from `alembic upgrade head` (not `create_all`, which would miss the 0004 scenario
columns the API serves).
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from testcontainers.postgres import PostgresContainer

_PG = PostgresContainer("postgres:16", driver="psycopg")
_PG.start()
os.environ["DATABASE_URL"] = _PG.get_connection_url()
os.environ["DB_SCHEMA"] = "gas_balance"
os.environ["APP_ENV"] = "local"

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from gasbalance_api.main import app
from gasbalance_core.db import SessionLocal
from gasbalance_core.models import Covariate, Forecast, Observation, Scenario, Series

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "infra" / "db" / "alembic.ini"
command.upgrade(Config(str(_ALEMBIC_INI)), "head")

_TABLES = (
    "forecast, observation, covariate, forecast_covariate, scenario, series, forecast_run, etl_run"
)


@pytest.fixture(scope="session", autouse=True)
def _container() -> Iterator[None]:
    yield
    _PG.stop()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    with SessionLocal() as s:
        s.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        s.commit()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class Factory:
    """Seeds committed rows so the app's own session sees them. FK order is handled here."""

    def __init__(self, session: Session) -> None:
        self.s = session

    def series(self, code: str, **kw: Any) -> int:
        obj = Series(code=code, name=kw.pop("name", code), source=kw.pop("source", "test"), **kw)
        self.s.add(obj)
        self.s.commit()
        return int(obj.id)

    def scenario(self, code: str, kind: str = "weather", **kw: Any) -> None:
        self.s.add(Scenario(code=code, kind=kind, **kw))
        self.s.commit()

    def observation(self, series_id: int, day: dt.date, value: float) -> None:
        self.s.add(Observation(series_id=series_id, obs_date=day, value=value))
        self.s.commit()

    def covariate(self, series_id: int, ts: dt.datetime, value: float) -> None:
        self.s.add(Covariate(series_id=series_id, ts=ts, value=value))
        self.s.commit()

    def forecast(
        self,
        series_id: int,
        scenario: str,
        target_date: dt.date,
        made_on: dt.date,
        value: float,
        model_run_id: str = "m1",
    ) -> None:
        if self.s.get(Scenario, scenario) is None:  # satisfy the forecast->scenario FK
            self.s.add(Scenario(code=scenario, kind="custom" if "@" in scenario else "weather"))
            self.s.commit()
        self.s.add(
            Forecast(
                series_id=series_id,
                scenario=scenario,
                target_date=target_date,
                made_on=made_on,
                value=value,
                model_run_id=model_run_id,
            )
        )
        self.s.commit()


@pytest.fixture
def factory() -> Iterator[Factory]:
    s = SessionLocal()
    try:
        yield Factory(s)
    finally:
        s.close()
