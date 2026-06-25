"""ORM models = the data contract at the DB shape level (single source of truth).

Producers (etl, ml) write; the api reads. The DB constraints here are the hard
floor: bad data physically cannot land. See docs/data-contracts.md.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from gasbalance_core.db import Base

# Reject NaN/±Inf at the DB floor — one NaN poisons every downstream sum.
# (In Postgres NaN compares equal to itself, so `<> 'NaN'` correctly rejects it.)
_FINITE = (
    "value <> 'NaN'::double precision "
    "AND value <> 'Infinity'::double precision "
    "AND value <> '-Infinity'::double precision"
)


class EtlRun(Base):
    """Audit row per ingestion run."""

    __tablename__ = "etl_run"

    run_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    rows_in: Mapped[int | None] = mapped_column(Integer)
    rows_loaded: Mapped[int | None] = mapped_column(Integer)
    rows_rejected: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)


class ForecastRun(Base):
    """Audit row per forecast-generation run."""

    __tablename__ = "forecast_run"

    run_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    scenario: Mapped[str | None] = mapped_column(Text)
    model_run_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    message: Mapped[str | None] = mapped_column(Text)


class Series(Base):
    """The series dictionary — the YAML config, materialized."""

    __tablename__ = "series"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)  # business id, e.g. "74.1"
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)  # was 'group' (reserved word)
    sub_group: Mapped[str | None] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text, nullable=False, server_default="mcm")
    source: Mapped[str] = mapped_column(Text, nullable=False)
    is_derived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (UniqueConstraint("code", name="uq_series_code"),)


class Scenario(Base):
    """Forecast scenarios (normal, weather replays, custom)."""

    __tablename__ = "scenario"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class Observation(Base):
    """Actuals — latest-only (upsert overwrites). loaded_at/run_id give provenance."""

    __tablename__ = "observation"

    series_id: Mapped[int] = mapped_column(
        ForeignKey(Series.id, name="fk_observation_series"), primary_key=True
    )
    obs_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Double, nullable=False)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey(EtlRun.run_id, name="fk_observation_etl_run")
    )
    loaded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (CheckConstraint(_FINITE, name="ck_observation_value_finite"),)


class Forecast(Base):
    """Forecasts — one vintage per made_on day; a new run overrides that day."""

    __tablename__ = "forecast"

    series_id: Mapped[int] = mapped_column(
        ForeignKey(Series.id, name="fk_forecast_series"), primary_key=True
    )
    target_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    scenario: Mapped[str] = mapped_column(
        ForeignKey(Scenario.code, name="fk_forecast_scenario"), primary_key=True
    )
    model_run_id: Mapped[str] = mapped_column(Text, primary_key=True)  # MLflow run id
    made_on: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Double, nullable=False)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey(ForecastRun.run_id, name="fk_forecast_forecast_run")
    )

    __table_args__ = (
        CheckConstraint(_FINITE, name="ck_forecast_value_finite"),
        Index("ix_forecast_latest", "series_id", "scenario", "target_date", "made_on"),
    )
