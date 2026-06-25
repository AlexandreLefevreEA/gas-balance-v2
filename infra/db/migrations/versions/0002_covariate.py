"""covariate table — sub-daily exogenous drivers (e.g. temperature)

Revision ID: 0002_covariate
Revises: 0001_initial
Create Date: 2026-06-25

Mirrors gasbalance_core.models.Covariate. Additive: an hourly-grained driver store
keyed by `(series_id, ts)`, separate from the daily `observation` actuals so a covariate
can hold 24 values/day. See ADR 0008.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from gasbalance_core.config import get_settings

revision: str = "0002_covariate"
down_revision: str | None = "0001_initial"
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
        "covariate",
        sa.Column("series_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("run_id", sa.BigInteger()),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("series_id", "ts", name="pk_covariate"),
        sa.ForeignKeyConstraint(["series_id"], [f"{SCHEMA}.series.id"], name="fk_covariate_series"),
        sa.ForeignKeyConstraint(
            ["run_id"], [f"{SCHEMA}.etl_run.run_id"], name="fk_covariate_etl_run"
        ),
        sa.CheckConstraint(_FINITE, name="ck_covariate_value_finite"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("covariate", schema=SCHEMA)
