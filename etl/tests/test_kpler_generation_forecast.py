"""Kpler generation-forecast connector contract test — fixture-based, no live network, no DB.

Covers the (zone, fuel, model)→canonical mapping carrying `made_on`, the wind onshore+offshore
fold (within one zone/model/vintage), that multiple vintages of one delivery hour coexist (the
point of the forecast store), the unknown zone/fuel/model drop, the MW sanity band, the
retention rule (`_vintages_to_delete`), and the history-floored fetch keep-set
(`_desired_run_dates`). (Retry/backoff is shared and tested in test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_generation_forecast import connector as kp
from gasbalance_etl.validation.forecast_covariate import (
    forecast_covariate_generation_schema as schema,
)


def _raw(
    rows: list[tuple[str, str, str, float | None]],
    *,
    date: str = "2026-07-01T00:00:00",
    made_on: str = "2026-06-24",
) -> pd.DataFrame:
    """Build a raw [zone, fuelType, model, date, value, made_on] frame like fetch() returns."""
    return pd.DataFrame(
        [
            {
                "zone": z,
                "fuelType": f,
                "model": m,
                "date": pd.Timestamp(date),
                "value": v,
                "made_on": pd.Timestamp(made_on),
            }
            for z, f, m, v in rows
        ],
        columns=["zone", "fuelType", "model", "date", "value", "made_on"],
    )


def test_code_includes_fuel_zone_and_model() -> None:
    assert kp._code("FR", "SOLAR", "EC_AIFS_ENS") == "KP.GENFC.SOLAR.FR.EC_AIFS_ENS"
    assert kp._code("DE", "WIND", "EC_46") == "KP.GENFC.WIND.DE.EC_46"


def test_series_dict_has_four_fuels_and_two_models_per_zone() -> None:
    sd = kp.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.GENFC.SOLAR.FR.EC_AIFS_ENS", "KP.GENFC.WIND.FR.EC_46"} <= codes
    assert len(sd) % 8 == 0  # exactly 4 fuels x 2 models per zone
    assert {e["sub_group"] for e in sd} == {"solar", "wind", "ror", "gas"}
    assert {e["group"] for e in sd} == {"generation_forecast"}
    assert {e["model"] for e in sd} == {"EC_AIFS_ENS", "EC_46"}


def test_to_canonical_folds_wind_maps_series_and_carries_made_on() -> None:
    df = kp.to_canonical(
        _raw(
            [
                ("FR", "solar", "EC_AIFS_ENS", 100.0),
                ("FR", "wind onshore", "EC_AIFS_ENS", 50.0),
                ("FR", "wind offshore", "EC_AIFS_ENS", 30.0),  # summed with onshore -> WIND 80
                ("FR", "fossil gas", "EC_AIFS_ENS", 200.0),
                ("FR", "hydro run-of-river and poundage", "EC_AIFS_ENS", 10.0),
                # DE-LU = Germany's bidding zone here; different zone+model -> own series
                ("DE-LU", "wind onshore", "EC_46", 70.0),
            ]
        )
    )
    by_id = dict(zip(df["series_id"], df["value"], strict=True))
    assert by_id["KP.GENFC.WIND.FR.EC_AIFS_ENS"] == 80.0  # onshore + offshore folded
    assert by_id["KP.GENFC.SOLAR.FR.EC_AIFS_ENS"] == 100.0
    assert by_id["KP.GENFC.GAS.FR.EC_AIFS_ENS"] == 200.0
    assert by_id["KP.GENFC.ROR.FR.EC_AIFS_ENS"] == 10.0
    assert by_id["KP.GENFC.WIND.DE-LU.EC_46"] == 70.0  # DE->DE-LU remap; not folded into FR wind
    assert (df["source"] == "kpler_generation_forecast").all()
    assert (df["group"] == "generation_forecast").all()
    assert "made_on" in df.columns
    schema.validate(df, lazy=True)  # hourly ts + made_on + plausible MW -> passes


def test_multiple_vintages_of_same_hour_coexist() -> None:
    # Same (date, series_id) from two run dates: would fail the canonical unique(date,
    # series_id), but the forecast schema keys on (made_on, date, series_id) -> passes.
    a = kp.to_canonical(_raw([("FR", "solar", "EC_46", 17.0)], made_on="2026-06-23"))
    b = kp.to_canonical(_raw([("FR", "solar", "EC_46", 18.0)], made_on="2026-06-24"))
    df = pd.concat([a, b], ignore_index=True)
    schema.validate(df, lazy=True)


def test_unmapped_fuel_unknown_zone_and_null_dropped() -> None:
    df = kp.to_canonical(
        _raw(
            [
                ("FR", "solar", "EC_46", 10.0),  # the one valid row
                ("FR", "nuclear", "EC_46", 900.0),  # fuel we don't track -> dropped
                ("ZZ", "solar", "EC_46", 10.0),  # zone not in the dictionary -> dropped
                ("FR", "solar", "EC_46", None),  # null value -> dropped (SOLAR.FR stays 10)
            ]
        )
    )
    assert list(df["series_id"]) == ["KP.GENFC.SOLAR.FR.EC_46"]
    assert df.loc[0, "value"] == 10.0


def test_unknown_model_is_dropped() -> None:
    assert kp.to_canonical(_raw([("FR", "solar", "EC_NOPE", 10.0)])).empty


def test_small_negative_is_allowed() -> None:
    # Kpler's feed can carry small metering-noise negatives; the schema must not reject them.
    df = kp.to_canonical(_raw([("FR", "fossil gas", "EC_46", -1.0)]))
    schema.validate(df, lazy=True)  # no raise


def test_absurd_value_is_blocked() -> None:
    df = kp.to_canonical(_raw([("FR", "solar", "EC_46", 1e9)]))  # e.g. a W-not-MW scale mistake
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


def test_desired_run_dates_clamps_to_history_start() -> None:
    today = dt.date(2026, 6, 25)  # Thursday, well after _HISTORY_START
    desired = set(kp._desired_run_dates(today))

    for i in range(16):  # every day in the last 15 days (incl. today)
        assert (today - dt.timedelta(days=i)) in desired

    monday = today - dt.timedelta(days=90)  # a Monday ~3 months back, after the floor -> weekly
    while monday.weekday() != 0:
        monday -= dt.timedelta(days=1)
    assert monday >= kp._HISTORY_START
    assert monday in desired

    pre_floor_monday = kp._HISTORY_START - dt.timedelta(days=1)  # a Monday before the floor
    while pre_floor_monday.weekday() != 0:
        pre_floor_monday -= dt.timedelta(days=1)
    assert pre_floor_monday not in desired  # floored out, even within the trailing year

    assert min(desired) >= kp._HISTORY_START  # nothing predates the data
    assert max(desired) <= today  # nothing in the future
