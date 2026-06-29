"""Spot-price schema = canonical + a generous EUR/MWh sanity band.

Kpler reports hourly day-ahead auction prices (EUR/MWh) per zone. Day-ahead prices
**legitimately go negative** (renewable oversupply; EPEX floors around -500 EUR/MWh), so the
lower bound sits a cushion below that. The upper bound (10 000 EUR/MWh) sits above the SDAC
harmonised maximum clearing price — which escalates by 1 000 each time it's hit, and the
2022 history contains a real 6 101.78 EUR/MWh print — yet far below a scale/unit error
(e.g. EUR/kWh-vs-EUR/MWh, ~1e3+ off), so it catches mistakes while passing every plausible row.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

spot_price_schema = canonical_schema.update_column(
    "value", checks=pa.Check.in_range(-1_000.0, 10_000.0)
)
