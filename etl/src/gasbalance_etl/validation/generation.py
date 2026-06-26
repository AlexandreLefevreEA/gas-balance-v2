"""Generation schema = canonical + a generous MW sanity band.

Kpler reports hourly generation in MW per zone/fuel. The feed occasionally carries small
negative metering noise (a few MW), so we deliberately do **not** floor at 0 — that would
reject real rows and, under lazy validation, block the whole load. The band instead catches
scale/unit mistakes (a W-not-MW read is ~1e6+) and absurd values, while passing every
plausible European zone/fuel hourly total. Hourly `date` timestamps validate under
canonical's `datetime64[ns]` / unique (date, series_id) rules unchanged.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

# MW. Lower bound tolerates metering-noise negatives; upper bound (200 GW) sits well above
# any single EU zone/fuel hourly total yet far below a W-vs-MW scale error. Tighten if
# false rejections ever appear.
generation_schema = canonical_schema.update_column(
    "value", checks=pa.Check.in_range(-10_000.0, 200_000.0)
)
