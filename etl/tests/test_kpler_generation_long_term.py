"""Kpler long-term generation connector contract test — fixture-based, no live network, no DB.

Covers the dynamic model list (MEAN + last-10 REF years), the (zone, fuel, model)→canonical
mapping (fuel already tagged as our code by fetch — no folding), the unknown zone/fuel/model
drop, and the MW sanity band. (Retry/backoff is shared and tested in test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_generation_long_term import connector as kp
from gasbalance_etl.validation.generation import generation_schema as schema


def test_models_is_mean_plus_last_ten_ref_years() -> None:
    models = kp._models()
    y = dt.date.today().year
    assert models[0] == "MEAN"
    assert len(models) == 11
    assert models[1:] == [f"REF_{yr}" for yr in range(y - 10, y)]


def test_code_includes_fuel_zone_model_and_strips_ref_underscore() -> None:
    assert kp._code("FR", "SOLAR", "MEAN") == "KP.GENLT.SOLAR.FR.MEAN"
    assert kp._code("DE-LU", "WIND", "REF_2020") == "KP.GENLT.WIND.DE-LU.REF2020"


def test_series_dict_has_three_fuels_and_eleven_models_per_zone() -> None:
    sd = kp.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.GENLT.SOLAR.FR.MEAN", "KP.GENLT.WIND.DE-LU.REF2020"} <= codes
    assert {e["sub_group"] for e in sd} == {"solar", "wind", "ror"}  # renewables, no gas
    assert {e["group"] for e in sd} == {"generation_longterm"}
    assert len(sd) % 33 == 0  # exactly 3 fuels x 11 models per zone


def _raw(
    zone: str = "FR", fuel: str = "SOLAR", model: str = "MEAN", value: float = 100.0
) -> pd.DataFrame:
    """A raw [zone, fuel, model, date, value] frame like fetch() returns (fuel already our code)."""
    return pd.DataFrame(
        {
            "zone": [zone],
            "fuel": [fuel],
            "model": [model],
            "date": pd.to_datetime(["2026-07-01T12:00:00"]),
            "value": [value],
        }
    )


def test_to_canonical_maps_zone_fuel_model_to_series() -> None:
    df = kp.to_canonical(_raw("DE-LU", "WIND", "REF_2020", 4200.0))
    assert df.loc[0, "series_id"] == "KP.GENLT.WIND.DE-LU.REF2020"
    assert df.loc[0, "sub_group"] == "wind"
    assert df.loc[0, "area"] == "DE"  # YAML maps area DE -> zone DE-LU
    assert df.loc[0, "group"] == "generation_longterm"
    assert (df["source"] == "kpler_generation_long_term").all()
    schema.validate(df, lazy=True)  # hourly timestamp + plausible MW -> passes


def test_unknown_zone_fuel_and_model_are_dropped() -> None:
    assert kp.to_canonical(_raw("ZZ", "SOLAR", "MEAN")).empty  # zone not in dictionary
    assert kp.to_canonical(_raw("FR", "GAS", "MEAN")).empty  # gas isn't a long-term fuel
    assert kp.to_canonical(_raw("FR", "SOLAR", "REF_1700")).empty  # out-of-window weather year


def test_small_negative_is_allowed() -> None:
    # Renewable feeds can carry small metering-noise negatives; the MW band must not reject them.
    schema.validate(kp.to_canonical(_raw("FR", "SOLAR", "MEAN", -1.0)), lazy=True)


def test_absurd_value_is_blocked() -> None:
    df = kp.to_canonical(_raw("FR", "SOLAR", "MEAN", 1e9))  # e.g. a W-not-MW scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
