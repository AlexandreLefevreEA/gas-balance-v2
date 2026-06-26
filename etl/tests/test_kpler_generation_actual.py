"""Kpler generation connector contract test — fixture-based, no live network, no DB.

Covers the (zone, fuel) → canonical mapping, the wind onshore+offshore fold, dropping
unmapped fuels / unknown zones / nulls, and the MW sanity band (an absurd value is
rejected; a small metering-noise negative is allowed — the feed reports those).
"""

from __future__ import annotations

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_generation_actual import connector as gen
from gasbalance_etl.validation.generation import generation_schema as schema

_TS = pd.Timestamp("2026-06-20T00:00:00")  # naive UTC, as fetch() produces


def _raw(rows: list[tuple[str, str, float | None]]) -> pd.DataFrame:
    """Build a raw [zone, fuelType, date, value] frame (one hour) like fetch() returns."""
    return pd.DataFrame(
        [{"zone": z, "fuelType": f, "date": _TS, "value": v} for z, f, v in rows],
        columns=["zone", "fuelType", "date", "value"],
    )


def test_series_dict_has_four_fuels_per_zone() -> None:
    sd = gen.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.GEN.SOLAR.FR", "KP.GEN.WIND.FR", "KP.GEN.ROR.FR", "KP.GEN.GAS.FR"} <= codes
    assert len(sd) % 4 == 0  # exactly 4 fuels per zone
    assert {e["sub_group"] for e in sd} == {"solar", "wind", "ror", "gas"}
    assert {e["group"] for e in sd} == {"generation"}


def test_to_canonical_folds_wind_and_maps_series() -> None:
    df = gen.to_canonical(
        _raw(
            [
                ("FR", "solar", 100.0),
                ("FR", "wind onshore", 50.0),
                ("FR", "wind offshore", 30.0),  # summed with onshore -> WIND 80
                ("FR", "fossil gas", 200.0),
                ("FR", "hydro run-of-river and poundage", 10.0),
            ]
        )
    )
    by_id = dict(zip(df["series_id"], df["value"], strict=True))
    assert by_id["KP.GEN.WIND.FR"] == 80.0  # onshore + offshore folded
    assert by_id["KP.GEN.SOLAR.FR"] == 100.0
    assert by_id["KP.GEN.GAS.FR"] == 200.0
    assert by_id["KP.GEN.ROR.FR"] == 10.0
    assert (df["source"] == "kpler_generation_actual").all()
    assert (df["group"] == "generation").all()
    schema.validate(df, lazy=True)  # hourly timestamp + plausible MW -> passes


def test_unmapped_fuel_unknown_zone_and_null_dropped() -> None:
    df = gen.to_canonical(
        _raw(
            [
                ("FR", "solar", 10.0),  # the one valid row
                ("FR", "nuclear", 900.0),  # fuel we don't track -> dropped
                ("ZZ", "solar", 10.0),  # zone not in the dictionary -> dropped
                ("FR", "solar", None),  # null value -> dropped (so SOLAR.FR stays 10)
            ]
        )
    )
    assert list(df["series_id"]) == ["KP.GEN.SOLAR.FR"]
    assert df.loc[0, "value"] == 10.0


def test_small_negative_is_allowed() -> None:
    # Kpler's feed carries small metering-noise negatives; the schema must not reject them.
    df = gen.to_canonical(_raw([("FR", "fossil gas", -1.0)]))
    schema.validate(df, lazy=True)  # no raise


def test_absurd_value_is_blocked() -> None:
    df = gen.to_canonical(_raw([("FR", "solar", 1e9)]))  # e.g. a W-not-MW scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
