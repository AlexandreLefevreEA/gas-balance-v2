"""Price schema = canonical + a generous EUR/MWh sanity band.

Kpler reports power **forward-curve** prices in EUR/MWh per zone. Forward (settlement-based)
prices are far smoother than spot, but hourly/daily-shaped curves legitimately dip negative in
high-renewable hours (observed ~-22 EUR/MWh), so we do **not** floor at 0. The band instead
catches scale/unit mistakes (a price expressed per kWh, or a W-vs-MW-style error, lands orders
of magnitude off) while passing every plausible European forward — even the 2022 crisis peaks
sat a few hundred EUR/MWh, well under the 5_000 ceiling.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

# EUR/MWh. Lower bound tolerates negative shaped-curve hours; upper bound (5_000) sits well above
# any plausible EU forward yet far below a unit/scale error. Tighten if false rejections appear.
price_schema = canonical_schema.update_column("value", checks=pa.Check.in_range(-1_000.0, 5_000.0))
