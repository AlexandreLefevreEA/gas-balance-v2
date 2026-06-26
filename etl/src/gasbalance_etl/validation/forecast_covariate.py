"""Forecast-covariate schemas = a per-source canonical schema + a `made_on` vintage column.

A forecast keeps many vintages (run dates) of the same delivery hour, so the canonical
`unique(date, series_id)` no longer holds — it becomes `unique(made_on, date, series_id)`.
Everything else (column shape, the source's value guard) is reused unchanged from the matching
non-forecast schema: `temperature_schema` ([-60, 60] °C) for temperature forecasts,
`generation_schema` ([-10_000, 200_000] MW) for generation forecasts, and `demand_schema`
([-10_000, 200_000] MW) for power-demand forecasts. Used by forecast connectors that load into
the vintage-keyed `forecast_covariate` table. See ADR 0009.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.demand import demand_schema
from gasbalance_etl.validation.generation import generation_schema
from gasbalance_etl.validation.temperature import temperature_schema

forecast_covariate_temperature_schema = pa.DataFrameSchema(
    name="forecast_covariate_temperature",
    columns={
        **temperature_schema.columns,
        "made_on": pa.Column("datetime64[ns]", nullable=False),  # the forecast run date
    },
    unique=["made_on", "date", "series_id"],  # a vintage, a delivery hour, a series
    coerce=True,
    strict=False,
)

forecast_covariate_generation_schema = pa.DataFrameSchema(
    name="forecast_covariate_generation",
    columns={
        **generation_schema.columns,
        "made_on": pa.Column("datetime64[ns]", nullable=False),  # the forecast run date
    },
    unique=["made_on", "date", "series_id"],  # a vintage, a delivery hour, a series
    coerce=True,
    strict=False,
)

forecast_covariate_demand_schema = pa.DataFrameSchema(
    name="forecast_covariate_demand",
    columns={
        **demand_schema.columns,
        "made_on": pa.Column("datetime64[ns]", nullable=False),  # the forecast run date
    },
    unique=["made_on", "date", "series_id"],  # a vintage, a delivery hour, a series
    coerce=True,
    strict=False,
)
