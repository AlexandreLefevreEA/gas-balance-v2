"""The canonical series schema — shared by every connector (docs/data-contracts.md).

Enforces only the *universal* invariants: the column shape, finite values (mirrors the
DB `ck_observation_value_finite` check), and no duplicate `(date, series_id)`.
Source-specific checks (storage >= 0, flow <= capacity, …) belong in the per-source
schema, not here.
"""

from __future__ import annotations

import math

import pandera.pandas as pa

_finite = pa.Check(
    lambda v: math.isfinite(v),
    element_wise=True,
    error="value must be finite (no NaN/Inf)",
)

canonical_schema = pa.DataFrameSchema(
    name="canonical",
    columns={
        "date": pa.Column("datetime64[ns]", nullable=False),
        "series_id": pa.Column(str, nullable=False),
        "name": pa.Column(str, nullable=False),
        "group": pa.Column(str, nullable=True),
        "sub_group": pa.Column(str, nullable=True),
        "area": pa.Column(str, nullable=True),
        "value": pa.Column(float, checks=_finite, nullable=False),
        "source": pa.Column(str, nullable=False),
        "loaded_at": pa.Column("datetime64[ns]", nullable=True, required=False),
    },
    unique=["date", "series_id"],  # no duplicate (date, series)
    coerce=True,
    strict=False,
)
