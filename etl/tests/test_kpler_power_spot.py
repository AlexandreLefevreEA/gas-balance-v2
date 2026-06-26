"""Kpler day-ahead price connector contract test — fixture-based, no live network, no DB.

Covers the zone -> canonical mapping (incl. the bidding-zone remaps DE-LU / IT-NORTH), dropping
unknown zones (e.g. the empty IT-PUN) and nulls, and the EUR/MWh sanity band — a negative price
passes (day-ahead prices legitimately go negative); an absurd value is rejected.
"""

from __future__ import annotations

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_power_spot import connector as spot
from gasbalance_etl.validation.spot_price import spot_price_schema as schema

_TS = pd.Timestamp("2026-06-20T00:00:00")  # naive UTC, as fetch() produces


def _raw(rows: list[tuple[str, float | None]]) -> pd.DataFrame:
    """Build a raw [zone, date, value] frame (one hour) like fetch() returns."""
    return pd.DataFrame(
        [{"zone": z, "date": _TS, "value": v} for z, v in rows],
        columns=["zone", "date", "value"],
    )


def test_series_dict_one_price_per_zone() -> None:
    sd = spot.series_dict()
    codes = {e["code"] for e in sd}
    assert "KP.SPOT.FR" in codes
    assert "KP.SPOT.DE-LU" in codes  # Germany remapped to the DE-LU bidding zone
    assert "KP.SPOT.IT-NORTH" in codes  # Italy remapped to IT-NORTH
    assert {e["group"] for e in sd} == {"price"}
    assert {e["sub_group"] for e in sd} == {"day_ahead"}
    assert len(sd) == 18  # one series per balance area


def test_to_canonical_maps_zones() -> None:
    df = spot.to_canonical(_raw([("FR", 42.0), ("DE-LU", 55.5), ("IT-NORTH", 88.0)]))
    by_id = dict(zip(df["series_id"], df["value"], strict=True))
    assert by_id["KP.SPOT.FR"] == 42.0
    assert by_id["KP.SPOT.DE-LU"] == 55.5  # remapped German zone
    assert by_id["KP.SPOT.IT-NORTH"] == 88.0  # remapped Italian zone
    assert (df["source"] == "kpler_power_spot").all()
    assert (df["group"] == "price").all()
    schema.validate(df, lazy=True)  # hourly timestamp + plausible EUR/MWh -> passes


def test_unknown_zone_and_null_dropped() -> None:
    df = spot.to_canonical(
        _raw(
            [
                ("FR", 42.0),  # the one valid row
                ("IT-PUN", 50.0),  # not in the dictionary (empty in the feed) -> dropped
                ("ZZ", 10.0),  # unknown zone -> dropped
                ("FR", None),  # null value -> dropped (so SPOT.FR stays 42)
            ]
        )
    )
    assert list(df["series_id"]) == ["KP.SPOT.FR"]
    assert df.loc[0, "value"] == 42.0


def test_negative_price_is_allowed() -> None:
    # Day-ahead prices legitimately go negative (renewable oversupply); the band must pass them.
    df = spot.to_canonical(_raw([("FR", -50.0)]))
    schema.validate(df, lazy=True)  # no raise


def test_absurd_value_is_blocked() -> None:
    df = spot.to_canonical(_raw([("FR", 1e9)]))  # e.g. a EUR/kWh-vs-EUR/MWh scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
