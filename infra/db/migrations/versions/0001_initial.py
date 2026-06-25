"""initial core schema (catalog + actuals + forecasts + audit)

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-25

Mirrors gasbalance_core.models. Covariate and derived-series tables come in later
migrations. No partitioning yet — the forecast retention policy caps volume; add
range-partitioning on made_on when row counts warrant it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from gasbalance_core.config import get_settings

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Schema name comes from config (DB_SCHEMA) — never hardcoded.
SCHEMA = get_settings().db_schema

_FINITE = (
    "value <> 'NaN'::double precision "
    "AND value <> 'Infinity'::double precision "
    "AND value <> '-Infinity'::double precision"
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "etl_run",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), server_default="running", nullable=False),
        sa.Column("rows_in", sa.Integer()),
        sa.Column("rows_loaded", sa.Integer()),
        sa.Column("rows_rejected", sa.Integer()),
        sa.Column("message", sa.Text()),
        schema=SCHEMA,
    )

    op.create_table(
        "forecast_run",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("scenario", sa.Text()),
        sa.Column("model_run_id", sa.Text()),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), server_default="running", nullable=False),
        sa.Column("message", sa.Text()),
        schema=SCHEMA,
    )

    op.create_table(
        "series",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text()),
        sa.Column("sub_group", sa.Text()),
        sa.Column("area", sa.Text()),
        sa.Column("unit", sa.Text(), server_default="mcm", nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("is_derived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.UniqueConstraint("code", name="uq_series_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "scenario",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "observation",
        sa.Column("series_id", sa.BigInteger(), nullable=False),
        sa.Column("obs_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("run_id", sa.BigInteger()),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("series_id", "obs_date", name="pk_observation"),
        sa.ForeignKeyConstraint(
            ["series_id"], [f"{SCHEMA}.series.id"], name="fk_observation_series"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], [f"{SCHEMA}.etl_run.run_id"], name="fk_observation_etl_run"
        ),
        sa.CheckConstraint(_FINITE, name="ck_observation_value_finite"),
        schema=SCHEMA,
    )

    op.create_table(
        "forecast",
        sa.Column("series_id", sa.BigInteger(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),
        sa.Column("model_run_id", sa.Text(), nullable=False),
        sa.Column("made_on", sa.Date(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("run_id", sa.BigInteger()),
        sa.PrimaryKeyConstraint(
            "series_id", "target_date", "scenario", "model_run_id", "made_on", name="pk_forecast"
        ),
        sa.ForeignKeyConstraint(["series_id"], [f"{SCHEMA}.series.id"], name="fk_forecast_series"),
        sa.ForeignKeyConstraint(
            ["scenario"], [f"{SCHEMA}.scenario.code"], name="fk_forecast_scenario"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], [f"{SCHEMA}.forecast_run.run_id"], name="fk_forecast_forecast_run"
        ),
        sa.CheckConstraint(_FINITE, name="ck_forecast_value_finite"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_forecast_latest",
        "forecast",
        ["series_id", "scenario", "target_date", "made_on"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("forecast", schema=SCHEMA)
    op.drop_table("observation", schema=SCHEMA)
    op.drop_table("scenario", schema=SCHEMA)
    op.drop_table("series", schema=SCHEMA)
    op.drop_table("forecast_run", schema=SCHEMA)
    op.drop_table("etl_run", schema=SCHEMA)
