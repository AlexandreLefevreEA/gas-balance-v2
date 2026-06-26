"""Kpler temperature-forecast connector contract test — fixture-based, no live network, no DB.

Covers the (zone, model)→canonical mapping carrying `made_on`, that multiple vintages of
one delivery hour coexist (the whole point of the forecast store), the unknown zone/model
drop, the temperature-range gate, the retention rule (`_vintages_to_delete`), and the fetch
keep-set (`_desired_run_dates`). (Retry/backoff is shared and tested in test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_temps_forecast import connector as kp
from gasbalance_etl.validation.forecast_covariate import (
    forecast_covariate_temperature_schema as schema,
)


def test_code_includes_zone_and_model() -> None:
    assert kp._code("FR", "EC_46") == "KP.TEMPFC.FR.EC_46"
    assert kp._code("DE", "EC_AIFS_ENS") == "KP.TEMPFC.DE.EC_AIFS_ENS"


def _raw(
    zone: str = "FR",
    model: str = "EC_46",
    value: float = 17.0,
    date: str = "2026-07-01T00:00:00",
    made_on: str = "2026-06-24",
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "zone": [zone],
            "model": [model],
            "date": pd.to_datetime([date]),
            "value": [value],
            "made_on": pd.to_datetime([made_on]),
        }
    )


def test_to_canonical_maps_zone_model_and_carries_made_on() -> None:
    df = kp.to_canonical(_raw("FR", "EC_46", 17.0))
    assert df.loc[0, "series_id"] == "KP.TEMPFC.FR.EC_46"
    assert df.loc[0, "sub_group"] == "EC_46"
    assert df.loc[0, "area"] == "FR"
    assert df.loc[0, "group"] == "temperature_forecast"
    assert (df["source"] == "kpler_temps_forecast").all()
    assert "made_on" in df.columns
    schema.validate(df, lazy=True)  # hourly ts + made_on + plausible value -> passes


def test_multiple_vintages_of_same_hour_coexist() -> None:
    # Same (date, series_id) from two run dates: would fail the canonical unique(date,
    # series_id), but the forecast schema keys on (made_on, date, series_id) -> passes.
    a = kp.to_canonical(_raw("FR", "EC_46", 17.0, made_on="2026-06-23"))
    b = kp.to_canonical(_raw("FR", "EC_46", 18.0, made_on="2026-06-24"))
    df = pd.concat([a, b], ignore_index=True)
    schema.validate(df, lazy=True)


def test_unknown_zone_is_dropped() -> None:
    assert kp.to_canonical(_raw("ZZ", "EC_46", 17.0)).empty


def test_unknown_model_is_dropped() -> None:
    assert kp.to_canonical(_raw("FR", "EC_NOPE", 17.0)).empty


def test_absurd_temperature_is_blocked() -> None:
    df = kp.to_canonical(_raw("FR", "EC_46", 999.0))  # e.g. a Kelvin mistake
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


def test_desired_run_dates_is_last_15_days_plus_mondays_of_the_year() -> None:
    today = dt.date(2026, 6, 25)  # Thursday
    desired = set(kp._desired_run_dates(today))

    for i in range(16):  # every day in the last 15 days (incl. today)
        assert (today - dt.timedelta(days=i)) in desired

    monday = today - dt.timedelta(days=180)  # a Monday ~6 months back -> kept weekly
    while monday.weekday() != 0:
        monday -= dt.timedelta(days=1)
    assert monday in desired

    nonmon = today - dt.timedelta(days=181)  # a non-Monday ~6 months back -> not fetched
    while nonmon.weekday() == 0:
        nonmon -= dt.timedelta(days=1)
    assert nonmon not in desired

    assert min(desired) >= today - dt.timedelta(days=365)  # nothing older than a year
    assert max(desired) <= today  # nothing in the future
