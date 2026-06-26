"""forecast_covariate table — hourly forecast covariates kept per vintage (made_on)

Revision ID: 0003_forecast_covariate
Revises: 0002_covariate
Create Date: 2026-06-25

Mirrors gasbalance_core.models.ForecastCovariate. Additive: like `covariate` but adds
`made_on` (the forecast run date) to the PK, so every daily vintage of a delivery hour is
retained, not overwritten — the store for multi-run weather forecasts. The connector's
retention policy prunes old vintages. See ADR 0009.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from gasbalance_core.config import get_settings

revision: str = "0003_forecast_covariate"
down_revision: str | None = "0002_covariate"
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
    op.create_table(
        "forecast_covariate",
        sa.Column("series_id", sa.BigInteger(), nullable=False),
        sa.Column("made_on", sa.Date(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("run_id", sa.BigInteger()),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("series_id", "made_on", "ts", name="pk_forecast_covariate"),
        sa.ForeignKeyConstraint(
            ["series_id"], [f"{SCHEMA}.series.id"], name="fk_forecast_covariate_series"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], [f"{SCHEMA}.etl_run.run_id"], name="fk_forecast_covariate_etl_run"
        ),
        sa.CheckConstraint(_FINITE, name="ck_forecast_covariate_value_finite"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_forecast_covariate_latest",
        "forecast_covariate",
        ["series_id", "ts", "made_on"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("forecast_covariate", schema=SCHEMA)
