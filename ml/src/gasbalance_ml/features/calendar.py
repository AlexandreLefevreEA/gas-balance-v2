"""Pure calendar + degree-day features. Pointwise in the date → no leakage surface."""

from __future__ import annotations

import numpy as np
import pandas as pd

# Heating/cooling degree-day base temperatures (°C). LDZ demand tracks HDD closely.
HDD_BASE = 15.5
CDD_BASE = 22.0


def degree_days(
    daily_temp: pd.Series, hdd_base: float = HDD_BASE, cdd_base: float = CDD_BASE
) -> pd.DataFrame:
    """HDD/CDD from a daily mean-temperature series (NaN where temp is missing)."""
    hdd = (hdd_base - daily_temp).clip(lower=0.0)
    cdd = (daily_temp - cdd_base).clip(lower=0.0)
    return pd.DataFrame({"hdd": hdd, "cdd": cdd})


def calendar_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Cyclical day-of-year + day-of-week + weekend flag for each date.

    ponytail: holidays deferred — they need a per-country calendar; add a `holidays`
    feature column when the LDZ fit shows residual structure on public holidays.
    """
    doy = index.dayofyear.to_numpy(dtype=float)
    dow = index.dayofweek.to_numpy(dtype=float)
    return pd.DataFrame(
        {
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25),
            "dow": dow,
            "is_weekend": (dow >= 5).astype(float),
        },
        index=index,
    )
