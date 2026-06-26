"""Demand schema = canonical + a generous MW sanity band.

Kpler reports hourly total electricity demand (MW) per zone — always positive in practice
(0 negatives across a 10-year FR pull). The small negative floor is a metering-noise cushion
(and tolerates the `residual_demand` loadType, which legitimately goes negative when
renewables exceed load). The upper bound (200 GW) sits well above any single EU zone's peak
load (FR/DE winter peaks ~80-100 GW) yet far below a W-vs-MW scale error (~1e6+), so it
catches unit/scale mistakes while passing every plausible row.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

demand_schema = canonical_schema.update_column(
    "value", checks=pa.Check.in_range(-10_000.0, 200_000.0)
)
