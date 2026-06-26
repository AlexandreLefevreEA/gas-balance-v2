"""Kpler availability-forecast (vintages) connector contract test — fixture-based, no live
network, no DB.

Covers the (zone, fuel)→canonical mapping carrying `made_on`, that multiple vintages of one
delivery day coexist (the point of the forecast store), the unmapped-fuel / unknown-zone / null
drop, the non-negative MW band, the retention rule (`_vintages_to_delete`), and the un-floored
fetch keep-set (`_desired_run_dates`). (Retry/backoff is shared and tested in test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_availability_forecast import connector as kp
from gasbalance_etl.validation.forecast_covariate import (
    forecast_covariate_availability_schema as schema,
)


def _raw(
    rows: list[tuple[str, str, float | None]],
    *,
    date: str = "2026-07-01T00:00:00",
    made_on: str = "2026-06-24",
) -> pd.DataFrame:
    """Build a raw [zone, fuelType, date, value, made_on] frame like fetch() returns."""
    return pd.DataFrame(
        [
            {
                "zone": z,
                "fuelType": f,
                "date": pd.Timestamp(date),
                "value": v,
                "made_on": pd.Timestamp(made_on),
            }
            for z, f, v in rows
        ],
        columns=["zone", "fuelType", "date", "value", "made_on"],
    )


def test_code_includes_fuel_and_zone() -> None:
    assert kp._code("FR", "NUCLEAR") == "KP.AVAILFC.NUCLEAR.FR"
    assert kp._code("DE", "LIGNITE") == "KP.AVAILFC.LIGNITE.DE"


def test_series_dict_four_fuels_per_zone_no_model() -> None:
    sd = kp.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.AVAILFC.NUCLEAR.FR", "KP.AVAILFC.GAS.DE"} <= codes
    assert {e["sub_group"] for e in sd} == {"coal", "gas", "lignite", "nuclear"}
    assert {e["group"] for e in sd} == {"availability_forecast"}
    assert len(sd) == 18 * 4  # 4 fuels per area, no model dimension


def test_to_canonical_maps_series_and_carries_made_on() -> None:
    df = kp.to_canonical(
        _raw(
            [
                ("FR", "nuclear", 45000.0),
                ("DE", "fossil brown coal/lignite", 8000.0),
                ("PL", "fossil hard coal", 15000.0),
            ]
        )
    )
    by_id = dict(zip(df["series_id"], df["value"], strict=True))
    assert by_id["KP.AVAILFC.NUCLEAR.FR"] == 45000.0
    assert by_id["KP.AVAILFC.LIGNITE.DE"] == 8000.0
    assert by_id["KP.AVAILFC.COAL.PL"] == 15000.0
    assert (df["source"] == "kpler_availability_forecast").all()
    assert (df["group"] == "availability_forecast").all()
    assert "made_on" in df.columns
    schema.validate(df, lazy=True)  # daily ts + made_on + plausible MW -> passes


def test_multiple_vintages_of_same_day_coexist() -> None:
    # Same (date, series_id) from two asOf snapshots: would fail the canonical unique(date,
    # series_id), but the forecast schema keys on (made_on, date, series_id) -> passes.
    a = kp.to_canonical(_raw([("FR", "nuclear", 44000.0)], made_on="2026-06-23"))
    b = kp.to_canonical(_raw([("FR", "nuclear", 45000.0)], made_on="2026-06-24"))
    df = pd.concat([a, b], ignore_index=True)
    schema.validate(df, lazy=True)


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
    assert list(df["series_id"]) == ["KP.AVAILFC.NUCLEAR.FR"]
    assert df.loc[0, "value"] == 45000.0


def test_negative_is_blocked() -> None:
    df = kp.to_canonical(_raw([("FR", "nuclear", -1.0)]))  # availability is non-negative
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)


def test_absurd_value_is_blocked() -> None:
    df = kp.to_canonical(_raw([("FR", "nuclear", 1e9)]))  # e.g. a W-not-MW scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)


def test_vintages_to_delete_applies_the_rule() -> None:
    today = dt.date(2026, 6, 25)  # Thursday
    keep_recent = today - dt.timedelta(days=10)  # within 15 days -> keep

    old_nonmon = today - dt.timedelta(days=40)  # outside window, non-Monday -> delete
    while old_nonmon.weekday() == 0:
        old_nonmon -= dt.timedelta(days=1)

    old_mon_in_year = today - dt.timedelta(days=100)  # Monday < 1y old -> keep
    while old_mon_in_year.weekday() != 0:
        old_mon_in_year -= dt.timedelta(days=1)

    old_mon_over_year = today - dt.timedelta(days=400)  # Monday > 1y old -> delete
    while old_mon_over_year.weekday() != 0:
        old_mon_over_year -= dt.timedelta(days=1)

    made_ons = [keep_recent, old_nonmon, old_mon_in_year, old_mon_over_year, today]
    assert kp._vintages_to_delete(made_ons, today) == {old_nonmon, old_mon_over_year}


def test_desired_run_dates_is_unfloored_keep_set() -> None:
    today = dt.date(2026, 6, 25)  # Thursday
    desired = set(kp._desired_run_dates(today))

    for i in range(16):  # every day in the last 15 days (incl. today)
        assert (today - dt.timedelta(days=i)) in desired

    monday = today - dt.timedelta(days=300)  # a Monday ~10 months back -> still kept (weekly)
    while monday.weekday() != 0:
        monday -= dt.timedelta(days=1)
    assert monday in desired

    # No history floor (asOf data is deep): the keep-set reaches a full year back.
    assert min(desired) <= today - dt.timedelta(days=300)
    assert max(desired) <= today  # nothing in the future
