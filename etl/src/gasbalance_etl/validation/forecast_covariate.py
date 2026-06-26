"""Forecast-covariate schemas = a per-source canonical schema + a `made_on` vintage column.

A forecast keeps many vintages (run dates) of the same delivery hour, so the canonical
`unique(date, series_id)` no longer holds — it becomes `unique(made_on, date, series_id)`.
Everything else (column shape, the source's value guard) is reused unchanged from the matching
non-forecast schema: `temperature_schema` ([-60, 60] °C) for temperature forecasts,
`generation_schema` ([-10_000, 200_000] MW) for generation forecasts, `demand_schema`
([-10_000, 200_000] MW) for power-demand forecasts, `price_schema` ([-1_000, 5_000] EUR/MWh)
for power price-forward-curve forecasts, `availability_schema` ([0, 200_000] MW) for
plant-availability vintages (`made_on` = the `asOf` snapshot date), `carbon_schema`
([0, 1000] EUR/tCO2) for the carbon (EUA) futures-settlement anchors and the spline forward
curve, `coal_schema` ([0, 1000] USD/t) for the coal forward curve (`made_on` = the trading
date), and `gas_price_schema` ([-50, 3000] EUR/MWh or GBX/thm) for gas forward curves. Used by
forecast connectors that load into the vintage-keyed `forecast_covariate` table. See ADR 0009.
"""

from __future__ import annotations

import pandera.pandas as pa

from gasbalance_etl.validation.availability import availability_schema
from gasbalance_etl.validation.carbon import carbon_schema
from gasbalance_etl.validation.coal import coal_schema
from gasbalance_etl.validation.demand import demand_schema
from gasbalance_etl.validation.gas_price import gas_price_schema
from gasbalance_etl.validation.generation import generation_schema
from gasbalance_etl.validation.price import price_schema
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

forecast_covariate_availability_schema = pa.DataFrameSchema(
    name="forecast_covariate_availability",
    columns={
        **availability_schema.columns,
        "made_on": pa.Column("datetime64[ns]", nullable=False),  # the asOf snapshot date
    },
    unique=["made_on", "date", "series_id"],  # a vintage, a delivery day, a series
    coerce=True,
    strict=False,
)

forecast_covariate_power_price_schema = pa.DataFrameSchema(
    name="forecast_covariate_power_price",
    columns={
        **price_schema.columns,
        "made_on": pa.Column("datetime64[ns]", nullable=False),  # the trading date (vintage)
    },
    unique=["made_on", "date", "series_id"],  # a vintage, a delivery day, a series
    coerce=True,
    strict=False,
)

forecast_covariate_carbon_schema = pa.DataFrameSchema(
    name="forecast_covariate_carbon",
    columns={
        **carbon_schema.columns,
        "made_on": pa.Column("datetime64[ns]", nullable=False),  # the trading date (vintage)
    },
    unique=["made_on", "date", "series_id"],  # a vintage, a maturity/delivery day, a series
    coerce=True,
    strict=False,
)

forecast_covariate_coal_price_schema = pa.DataFrameSchema(
    name="forecast_covariate_coal_price",
    columns={
        **coal_schema.columns,
        "made_on": pa.Column("datetime64[ns]", nullable=False),  # the trading date (vintage)
    },
    unique=["made_on", "date", "series_id"],  # a vintage, a delivery day, a series
    coerce=True,
    strict=False,
)

forecast_covariate_gas_price_schema = pa.DataFrameSchema(
    name="forecast_covariate_gas_price",
    columns={
        **gas_price_schema.columns,
        "made_on": pa.Column("datetime64[ns]", nullable=False),  # the curve's trading date
    },
    unique=["made_on", "date", "series_id"],  # a vintage, a delivery day, a series
    coerce=True,
    strict=False,
)
