"""Kpler power-demand connector contract test — fixture-based, no live network, no DB.

Covers the zone → canonical mapping, dropping unknown zones / nulls, and the MW sanity band
(an absurd value is rejected; a plausible national load passes).
"""

from __future__ import annotations

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_power_demand import connector as dem
from gasbalance_etl.validation.demand import demand_schema as schema

_TS = pd.Timestamp("2026-06-20T00:00:00")  # naive UTC, as fetch() produces


def _raw(rows: list[tuple[str, float | None]]) -> pd.DataFrame:
    """Build a raw [zone, date, value] frame (one hour) like fetch() returns."""
    return pd.DataFrame(
        [{"zone": z, "date": _TS, "value": v} for z, v in rows],
        columns=["zone", "date", "value"],
    )


def test_series_dict_one_demand_series_per_zone() -> None:
    sd = dem.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.LOAD.FR", "KP.LOAD.DE"} <= codes
    assert len(codes) == len(sd)  # exactly one series per zone, no dups
    assert {e["group"] for e in sd} == {"demand"}
    assert {e["sub_group"] for e in sd} == {"demand"}


def test_to_canonical_maps_series() -> None:
    df = dem.to_canonical(_raw([("FR", 40014.0), ("DE", 37805.0)]))
    by_id = dict(zip(df["series_id"], df["value"], strict=True))
    assert by_id["KP.LOAD.FR"] == 40014.0
    assert by_id["KP.LOAD.DE"] == 37805.0
    assert (df["source"] == "kpler_power_demand").all()
    assert (df["group"] == "demand").all()
    schema.validate(df, lazy=True)  # hourly timestamp + plausible MW -> passes


def test_unknown_zone_and_null_dropped() -> None:
    df = dem.to_canonical(
        _raw(
            [
                ("FR", 40000.0),  # the one valid row
                ("ZZ", 1000.0),  # zone not in the dictionary -> dropped
                ("DE", None),  # null value -> dropped
            ]
        )
    )
    assert list(df["series_id"]) == ["KP.LOAD.FR"]
    assert df.loc[0, "value"] == 40000.0


def test_absurd_value_is_blocked() -> None:
    df = dem.to_canonical(_raw([("FR", 1e9)]))  # e.g. a W-not-MW scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
