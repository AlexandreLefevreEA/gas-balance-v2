"""Availability schema = canonical + a non-negative MW capacity band.

Kpler reports daily **available capacity** (MW) per zone/fuel (`central` estimate). Unlike
metered generation, availability is a derived capacity figure that is genuinely **≥ 0** (a
fleet that is fully out reports 0; a country with no such fleet reports a constant 0), so we
floor at 0 — a negative would be a data error and should block the load. The 200 GW ceiling
sits well above any single EU zone/fuel availability (FR nuclear peaks ~60 GW) yet far below a
W-vs-MW scale error. Daily `date` timestamps validate under canonical's `datetime64[ns]` /
unique (date, series_id) rules unchanged.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

# MW. [0, 200 GW]: availability is non-negative capacity (0 = fully out / no fleet); the ceiling
# clears the largest EU zone/fuel availability yet catches a scale mistake. Tighten if needed.
availability_schema = canonical_schema.update_column(
    "value", checks=pa.Check.in_range(0.0, 200_000.0)
)
