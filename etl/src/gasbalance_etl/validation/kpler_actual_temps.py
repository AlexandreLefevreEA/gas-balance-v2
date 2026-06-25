"""Per-source schema for Kpler temperatures = canonical + a plausible-range guard.

Bounds `value` to [-60, 60] °C. The range subsumes the canonical finite check (NaN/Inf
fail it) and also catches a unit mistake (e.g. Kelvin ~270). Hourly `date` timestamps
validate under the canonical `datetime64[ns]` / unique (date, series_id) rules unchanged.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

# Plausible European 2 m temperature, °C. Tighten if false rejections ever appear.
temperature_schema = canonical_schema.update_column(
    "value", checks=pa.Check.in_range(-60.0, 60.0)
)
