"""Gas-price schema = canonical + a wide forward-price sanity band.

Kpler quotes the forward gas curve in EUR/MWh for the continental hubs and GBX/thm (pence per
therm) for NBP. Forward gas is positive in practice (EUR/MWh peaked ~340 in Aug-2022, NBP ~800
GBX/thm), so the band keeps a small negative cushion and an upper bound well above any real spike
yet far below a unit/scale error (~1e6+) — it passes every plausible row in either currency while
catching scale mistakes. The quote currency is carried per series in `sub_group`.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

gas_price_schema = canonical_schema.update_column("value", checks=pa.Check.in_range(-50.0, 3000.0))
