"""Forecast-covariate schema = the temperature schema + a `made_on` vintage column.

A forecast keeps many vintages (run dates) of the same delivery hour, so the canonical
`unique(date, series_id)` no longer holds — it becomes `unique(made_on, date, series_id)`.
Everything else (column shape, finite + [-60, 60] °C guard) is reused from
`temperature_schema` unchanged. Used by forecast-temperature connectors that load into the
vintage-keyed `forecast_covariate` table. See ADR 0009.
"""

from __future__ import annotations

import pandera.pandas as pa

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
