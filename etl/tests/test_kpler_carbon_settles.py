"""Kpler carbon-settles connector contract test — fixture-based, no live network, no DB.

Covers the EUA-only filter (drops ETS2 / UKA and non-monthly / null rows), `made_on` carried,
multiple vintages of one maturity coexisting, the EUR/tCO2 sanity band, the retention rule
(`_vintages_to_delete`), and the fetch keep-set (`_desired_run_dates`). (Retry/backoff is shared
and tested in test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_carbon_settles import connector as settles
from gasbalance_etl.validation.forecast_covariate import (
    forecast_covariate_carbon_schema as schema,
)

_EUA = "EEX EUA Future"
_ETS2 = "EEX EU ETS2 Future"
_UKA = "EEX UKA Futures"


def _raw(
    rows: list[tuple[str, str, str, float | None]],
    made_on: str = "2026-06-25",
    ois: list[float] | None = None,
) -> pd.DataFrame:
    """Build a raw [date, long_name, maturity_type, value, open_interest, made_on] frame.

    `ois` sets per-row open interest (defaults to 100 each); only the collision test varies it.
    """
    ois = ois if ois is not None else [100.0] * len(rows)
    return pd.DataFrame(
        [
            {
                "date": pd.to_datetime(maturity),
                "long_name": long_name,
                "maturity_type": maturity_type,
                "value": value,
                "open_interest": oi,
                "made_on": pd.to_datetime(made_on),
            }
            for (maturity, long_name, maturity_type, value), oi in zip(rows, ois, strict=True)
        ],
        columns=["date", "long_name", "maturity_type", "value", "open_interest", "made_on"],
    )


def test_series_dict_single_settles_series() -> None:
    sd = settles.series_dict()
    assert len(sd) == 1
    assert sd[0]["code"] == "KP.CARBON.SETTLES"
    assert sd[0]["group"] == "carbon"
    assert sd[0]["sub_group"] == "eua_settles"
    assert sd[0]["unit"] == "EUR/tCO2"


def test_to_canonical_keeps_eua_drops_ets2_and_uka() -> None:
    df = settles.to_canonical(
        _raw(
            [
                ("2026-07-01", _EUA, "month", 79.68),
                ("2026-07-01", _ETS2, "month", 69.99),  # ETS2 (EUR/EUA2) -> dropped
                ("2026-12-01", _UKA, "month", 57.30),  # UKA (GBP/UKA) -> dropped
            ]
        )
    )
    assert set(df["series_id"]) == {"KP.CARBON.SETTLES"}
    assert list(df["value"]) == [79.68]
    assert (df["source"] == "kpler_carbon_settles").all()
    schema.validate(df, lazy=True)  # maturity ts + made_on + plausible EUA price -> passes


def test_non_monthly_and_null_dropped() -> None:
    df = settles.to_canonical(
        _raw(
            [
                ("2026-07-01", _EUA, "month", 79.68),
                ("2026-08-01", _EUA, "quarter", 80.0),  # defensive: only month is kept
                ("2026-09-01", _EUA, "month", None),  # null settlement dropped
            ]
        )
    )
    assert list(df["value"]) == [79.68]


def test_made_on_carried_and_multiple_vintages_coexist() -> None:
    # Same (maturity, series) settled on two trading dates: canonical unique(date, series_id) would
    # fail, but the forecast schema keys on (made_on, date, series_id) -> passes.
    a = settles.to_canonical(_raw([("2026-12-01", _EUA, "month", 80.57)], made_on="2026-06-23"))
    b = settles.to_canonical(_raw([("2026-12-01", _EUA, "month", 80.61)], made_on="2026-06-24"))
    df = pd.concat([a, b], ignore_index=True)
    assert "made_on" in df.columns
    schema.validate(df, lazy=True)


def test_empty_in_empty_out() -> None:
    empty = pd.DataFrame(
        columns=["date", "long_name", "maturity_type", "value", "open_interest", "made_on"]
    )
    assert settles.to_canonical(empty).empty


def test_absurd_value_is_blocked() -> None:
    df = settles.to_canonical(_raw([("2026-07-01", _EUA, "month", 1e6)]))  # e.g. a scale mistake
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
    assert settles._vintages_to_delete(made_ons, today) == {old_nonmon, old_mon_over_year}


def test_desired_run_dates_is_last_15_days_plus_mondays_of_the_year() -> None:
    today = dt.date(2026, 6, 25)  # Thursday
    desired = set(settles._desired_run_dates(today))

    for i in range(16):  # every day in the last 15 days (incl. today)
        assert (today - dt.timedelta(days=i)) in desired

    monday = today - dt.timedelta(days=180)  # a Monday ~6 months back -> kept weekly
    while monday.weekday() != 0:
        monday -= dt.timedelta(days=1)
    assert monday in desired

    assert min(desired) >= today - dt.timedelta(days=365)  # nothing older than a year
    assert max(desired) <= today  # nothing in the future


def test_overlapping_contracts_keep_most_liquid() -> None:
    # Kpler lists two distinct EEX contracts under one (made_on, maturity) — its maturityDate is
    # occasionally misassigned. Keep the liquid one (max open interest), the market reference,
    # NOT the higher price; the forecast unique(made_on, date, series_id) then holds.
    df = settles.to_canonical(
        _raw(
            [("2026-07-01", _EUA, "month", 70.02), ("2026-07-01", _EUA, "month", 68.19)],
            ois=[0.0, 209.0],
        )
    )
    assert list(df["value"]) == [68.19]  # the OI=209 contract, despite its lower price
    schema.validate(df, lazy=True)
