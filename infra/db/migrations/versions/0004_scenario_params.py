"""scenario custom-what-if parameters (kind + adjustments + weather_years)

Revision ID: 0004_scenario_params
Revises: 0003_forecast_covariate
Create Date: 2026-06-30

Mirrors gasbalance_core.models.Scenario. Additive: extends the existing `scenario` table
(no new table — ADR 0007) so a custom scenario ("EU demand +10%") stores its adjustment
rules + the weather scenarios it spans, readable by the running/balance layer and writable
by the api/web at runtime. `kind` distinguishes auto-seeded weather rows from authored
customs and their materialized `<custom>@<weather>` combos.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from gasbalance_core.config import get_settings

revision: str = "0004_scenario_params"
down_revision: str | None = "0003_forecast_covariate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Schema name comes from config (DB_SCHEMA) — never hardcoded.
SCHEMA = get_settings().db_schema


def upgrade() -> None:
    op.add_column(
        "scenario",
        sa.Column("kind", sa.Text(), server_default="weather", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "scenario", sa.Column("adjustments", postgresql.JSONB(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "scenario", sa.Column("weather_years", postgresql.JSONB(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("scenario", "weather_years", schema=SCHEMA)
    op.drop_column("scenario", "adjustments", schema=SCHEMA)
    op.drop_column("scenario", "kind", schema=SCHEMA)
