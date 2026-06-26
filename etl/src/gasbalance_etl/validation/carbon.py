"""Carbon schema = canonical + a plausible EUA price band (EUR/tCO2).

Kpler reports the EU Allowance (EUA) spot settlement price. The EUA has traded roughly EUR 3
(2017 lows) to ~EUR 100 (2023 peak); the band floors at 0 (a carbon price is strictly positive)
and caps at 1000 — far above any plausible price yet well below a scale error, so it catches
unit/scale mistakes while passing every real settlement.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

carbon_schema = canonical_schema.update_column("value", checks=pa.Check.in_range(0.0, 1000.0))
