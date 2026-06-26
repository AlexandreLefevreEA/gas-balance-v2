"""Gas spot-price schema = canonical + a generous EUR/MWh sanity band.

Kpler reports the EEX day-ahead gas settlement (EUR/MWh) per hub. European hub prices have
ranged from ~3 EUR/MWh (2020 lows) to ~340 EUR/MWh (the Aug-2022 crisis) and are effectively
never negative (unlike power). The small negative floor is a noise cushion; the 1000 EUR/MWh
ceiling sits far above any historical spot yet well below a unit/scale slip (a stray p/therm
value or a x1000 error), so it catches mistakes while passing every plausible row.
# ponytail: a calibration knob — retighten once a full backfill's real range is known.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

gas_spot_schema = canonical_schema.update_column("value", checks=pa.Check.in_range(-10.0, 1000.0))
