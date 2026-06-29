"""Coal-spot schema = canonical + a plausible API-2 coal price band (USD/t).

Energy Quantified reports the ICE API-2 (CIF ARA) front-month price. API-2 has traded roughly
USD 40 (mid-2010s/2020 lows) to ~USD 450 (the 2022 peak); the band floors at 0 (a coal price is
strictly positive) and caps at 1000 — far above any plausible price yet well below a scale
error, so it catches unit/scale mistakes while passing every real settlement.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

coal_spot_schema = canonical_schema.update_column("value", checks=pa.Check.in_range(0.0, 1000.0))
