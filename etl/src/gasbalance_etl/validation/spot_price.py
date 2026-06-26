"""Spot-price schema = canonical + a generous EUR/MWh sanity band.

Kpler reports hourly day-ahead auction prices (EUR/MWh) per zone. Day-ahead prices
**legitimately go negative** (renewable oversupply; EPEX floors around -500 EUR/MWh), so the
lower bound sits a cushion below that. The upper bound (5 000 EUR/MWh) is at the EU auction
price cap (~+4 000, raised toward +5 000) and above the worst 2022-crisis spikes, yet far
below a scale/unit error (e.g. EUR/kWh-vs-EUR/MWh, ~1e3+ off), so it catches mistakes while
passing every plausible row.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.canonical import canonical_schema

spot_price_schema = canonical_schema.update_column(
    "value", checks=pa.Check.in_range(-1_000.0, 5_000.0)
)
