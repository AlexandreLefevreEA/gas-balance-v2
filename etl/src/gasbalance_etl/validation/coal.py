"""Coal schema = canonical + a plausible API-2 coal price band (USD/t).

Energy Quantified reports the ICE **Coal API-2 (CIF ARA)** futures settlement in USD/t. API-2 has
traded roughly USD 40 (2020 lows) to ~USD 450 (2022 crisis peak); the band floors at 0 (a coal
price is strictly positive) and caps at 1000 — far above any plausible settlement yet well below a
unit/scale error, so it catches unit/scale mistakes while passing every real settle.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

coal_schema = canonical_schema.update_column("value", checks=pa.Check.in_range(0.0, 1000.0))
