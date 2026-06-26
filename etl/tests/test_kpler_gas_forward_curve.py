"""Kpler gas-forward-curve connector contract test — fixture-based, no live network, no DB.

Covers the hub->canonical mapping carrying `made_on`, that multiple vintages of one delivery day
coexist (the point of the forecast store), the unknown-hub drop, the currency band (EUR/MWh and
NBP's GBX/thm both pass, a W-scale value is blocked), the retention rule (`_vintages_to_delete`),
and the fetch keep-set (`_desired_run_dates`) skipping weekends. (Retry/backoff is shared and
tested in test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_gas_forward_curve import connector as kp
from gasbalance_etl.validation.forecast_covariate import (
    forecast_covariate_gas_price_schema as schema,
)


def test_code_is_hub() -> None:
    assert kp._code("TTF") == "KP.GASFC.TTF"
    assert kp._code("NBP") == "KP.GASFC.NBP"


def _raw(
    zone: str = "TTF",
    value: float = 40.667,
    date: str = "2026-07-01",
    made_on: str = "2026-06-24",
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "zone": [zone],
            "date": pd.to_datetime([date]),
            "value": [value],
            "made_on": pd.to_datetime([made_on]),
        }
    )


def test_series_dict_one_per_hub_with_currency() -> None:
    sd = kp.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.GASFC.TTF", "KP.GASFC.NBP"} <= codes
    assert len(sd) == len({e["hub"] for e in sd})  # exactly one series per hub
    assert {e["group"] for e in sd} == {"gas_forward_curve"}
    assert {e["sub_group"] for e in sd} == {"EUR/MWh", "GBX/thm"}
    nbp = next(e for e in sd if e["hub"] == "NBP")
    assert nbp["sub_group"] == "GBX/thm"
    assert nbp["area"] == "NBP"


def test_to_canonical_maps_hub_and_carries_made_on() -> None:
    df = kp.to_canonical(_raw("TTF", 40.667))
    assert df.loc[0, "series_id"] == "KP.GASFC.TTF"
    assert df.loc[0, "sub_group"] == "EUR/MWh"
    assert df.loc[0, "area"] == "TTF"
    assert df.loc[0, "group"] == "gas_forward_curve"
    assert (df["source"] == "kpler_gas_forward_curve").all()
    assert "made_on" in df.columns
    schema.validate(df, lazy=True)  # daily ts + made_on + plausible EUR/MWh -> passes


def test_nbp_pence_per_therm_passes() -> None:
    # NBP quotes in GBX/thm (~98), a different scale from EUR/MWh; the band must pass it.
    df = kp.to_canonical(_raw("NBP", 98.56))
    schema.validate(df, lazy=True)


def test_multiple_vintages_of_same_day_coexist() -> None:
    # Same (date, series_id) from two trading dates: would fail the canonical unique(date,
    # series_id), but the forecast schema keys on (made_on, date, series_id) -> passes.
    a = kp.to_canonical(_raw("TTF", 40.0, made_on="2026-06-23"))
    b = kp.to_canonical(_raw("TTF", 41.0, made_on="2026-06-24"))
    df = pd.concat([a, b], ignore_index=True)
    schema.validate(df, lazy=True)


def test_unknown_hub_is_dropped() -> None:
    assert kp.to_canonical(_raw("ZZZ", 40.0)).empty


def test_absurd_value_is_blocked() -> None:
    df = kp.to_canonical(_raw("TTF", 1e9))  # e.g. a unit/scale mistake
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


def test_desired_run_dates_skips_weekends() -> None:
    today = dt.date(2026, 6, 25)  # Thursday
    desired = set(kp._desired_run_dates(today))

    # No weekend trading date is requested (gas markets are closed) — the one custom bit.
    assert all(d.weekday() < 5 for d in desired)
    sat = dt.date(2026, 6, 20)  # a Saturday inside the 15-day window
    assert sat.weekday() == 5 and sat not in desired

    # Recent weekdays + Mondays of the year are still in the keep-set.
    assert today in desired
    monday = today - dt.timedelta(days=180)  # a Monday ~6 months back -> kept weekly
    while monday.weekday() != 0:
        monday -= dt.timedelta(days=1)
    assert monday in desired

    assert min(desired) >= today - dt.timedelta(days=365)  # nothing older than a year
    assert max(desired) <= today  # nothing in the future
