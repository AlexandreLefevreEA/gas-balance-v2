"""FX schema = canonical + a gross-error band for ECB euro reference rates.

The ECB publishes daily reference rates as units of foreign currency per 1 EUR. The curated
currencies (USD ~1.08, GBP ~0.85, NOK ~11 per EUR) all sit comfortably inside (0, 1000], so
the band is a gross-corruption / scale tripwire — the canonical finite check is the real
NaN/Inf guard. A rate is strictly positive, so the floor at 0 also catches a sign/zero slip.
# ponytail: calibration knob — raise the ceiling if a high-magnitude currency is added to the
# YAML (JPY ~170, HUF ~390, IDR ~17000 per EUR all exceed 1000).
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

fx_schema = canonical_schema.update_column("value", checks=pa.Check.in_range(0.0, 1000.0))
