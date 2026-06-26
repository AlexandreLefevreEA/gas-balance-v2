"""Kpler availability (actual) connector contract test — fixture-based, no live network, no DB.

Covers the (zone, fuel)→canonical mapping, dropping unmapped fuels / unknown zones / nulls, and
the non-negative MW band — a plausible value and a genuine 0 (fully-out / no-fleet) pass; a
negative and an absurd value are rejected.
"""

from __future__ import annotations

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_availability import connector as kp
from gasbalance_etl.validation.availability import availability_schema as schema

_TS = pd.Timestamp("2026-06-20T00:00:00")  # naive UTC daily, as fetch() produces


def _raw(rows: list[tuple[str, str, float | None]]) -> pd.DataFrame:
    """Build a raw [zone, fuelType, date, value] frame (one day) like fetch() returns."""
    return pd.DataFrame(
        [{"zone": z, "fuelType": f, "date": _TS, "value": v} for z, f, v in rows],
        columns=["zone", "fuelType", "date", "value"],
    )


def test_code_includes_fuel_and_zone() -> None:
    assert kp._code("FR", "NUCLEAR") == "KP.AVAIL.NUCLEAR.FR"
    assert kp._code("DE", "LIGNITE") == "KP.AVAIL.LIGNITE.DE"


def test_series_dict_four_fuels_per_zone() -> None:
    sd = kp.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.AVAIL.NUCLEAR.FR", "KP.AVAIL.GAS.DE"} <= codes
    assert {e["sub_group"] for e in sd} == {"coal", "gas", "lignite", "nuclear"}
    assert {e["group"] for e in sd} == {"availability"}
    assert len(sd) == 18 * 4  # 4 fuels per balance area


def test_to_canonical_maps_fuels_and_zones() -> None:
    df = kp.to_canonical(
        _raw(
            [
                ("FR", "nuclear", 45000.0),
                ("DE", "fossil brown coal/lignite", 8000.0),
                ("PL", "fossil hard coal", 15000.0),
                ("NL", "fossil gas", 13000.0),
            ]
        )
    )
    by_id = dict(zip(df["series_id"], df["value"], strict=True))
    assert by_id["KP.AVAIL.NUCLEAR.FR"] == 45000.0
    assert by_id["KP.AVAIL.LIGNITE.DE"] == 8000.0
    assert by_id["KP.AVAIL.COAL.PL"] == 15000.0
    assert by_id["KP.AVAIL.GAS.NL"] == 13000.0
    assert (df["source"] == "kpler_availability").all()
    assert (df["group"] == "availability").all()
    schema.validate(df, lazy=True)  # daily ts + plausible MW -> passes


def test_unmapped_fuel_unknown_zone_and_null_dropped() -> None:
    df = kp.to_canonical(
        _raw(
            [
                ("FR", "nuclear", 45000.0),  # the one valid row
                ("FR", "biomass", 900.0),  # fuel we don't track -> dropped
                ("ZZ", "nuclear", 100.0),  # zone not in the dictionary -> dropped
                ("FR", "nuclear", None),  # null value -> dropped (NUCLEAR.FR stays 45000)
            ]
        )
    )
    assert list(df["series_id"]) == ["KP.AVAIL.NUCLEAR.FR"]
    assert df.loc[0, "value"] == 45000.0


def test_zero_availability_is_allowed() -> None:
    # 0 is real signal: a fleet fully out, or a country with no such fleet (e.g. DE nuclear).
    df = kp.to_canonical(_raw([("DE", "nuclear", 0.0)]))
    schema.validate(df, lazy=True)  # no raise


def test_negative_is_blocked() -> None:
    # Availability is non-negative capacity; a negative is a data error and must block the load.
    df = kp.to_canonical(_raw([("FR", "nuclear", -1.0)]))
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)


def test_absurd_value_is_blocked() -> None:
    df = kp.to_canonical(_raw([("FR", "nuclear", 1e9)]))  # e.g. a W-not-MW scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
