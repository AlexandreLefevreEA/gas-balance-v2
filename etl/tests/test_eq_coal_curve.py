"""Energy Quantified coal forward-curve contract test — fixture-based, no live network, no DB.

Covers the single-series mapping carrying `made_on`, the monthly-only cubic-spline-to-daily
transform (contiguous daily grid, passes through the settles, no extrapolation past the monthly
strip, quarter/year contracts ignored), that multiple vintages of one delivery day coexist, the
USD/t band guard, the retention rule (`_vintages_to_delete`) and the weekday keep-set
(`_desired_run_dates`). (Retry/backoff is shared and tested in test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.eq_coal_curve import connector as eq
from gasbalance_etl.validation.forecast_covariate import (
    forecast_covariate_coal_price_schema as schema,
)


def _raw(
    months: int = 12,
    settle: float | list[float] = 100.0,
    made_on: str = "2025-12-05",
    start: str = "2026-01-01",
    extra: list[dict] | None = None,
) -> pd.DataFrame:
    """A monthly OHLC strip (period='month') as fetch() returns it, plus any `extra` contracts."""
    deliveries = pd.date_range(start, periods=months, freq="MS")
    settles = [settle] * months if isinstance(settle, int | float) else settle
    rows = [
        {"made_on": made_on, "period": "month", "delivery": d.strftime("%Y-%m-%d"), "value": v}
        for d, v in zip(deliveries, settles, strict=True)
    ]
    rows += extra or []
    df = pd.DataFrame(rows, columns=["made_on", "period", "delivery", "value"])
    df["delivery"] = pd.to_datetime(df["delivery"])
    df["made_on"] = pd.to_datetime(df["made_on"])
    return df


def test_series_dict_is_one_coal_curve() -> None:
    sd = eq.series_dict()
    assert len(sd) == 1
    (e,) = sd
    assert e["code"] == "EQ.COALFC.API2"
    assert e["group"] == "coal_forward_curve"
    assert e["sub_group"] == "USD/t"
    assert e["unit"] == "USD/t"
    assert e["area"] == "ARA"


def test_to_canonical_maps_and_carries_made_on() -> None:
    df = eq.to_canonical(_raw())
    assert (df["series_id"] == "EQ.COALFC.API2").all()
    assert (df["group"] == "coal_forward_curve").all()
    assert (df["source"] == "eq_coal_curve").all()
    assert "made_on" in df.columns
    assert (df["made_on"] == pd.Timestamp("2025-12-05")).all()
    schema.validate(df, lazy=True)  # daily ts + made_on + plausible USD/t -> passes


def test_spline_is_contiguous_daily_within_the_strip() -> None:
    df = eq.to_canonical(_raw(months=12, start="2026-01-01")).sort_values("date")
    days = df["date"]
    assert (days.diff().dropna() == pd.Timedelta(days=1)).all()  # no gaps
    # no extrapolation: stays inside the monthly-midpoint knot range
    assert days.min() >= pd.Timestamp("2026-01-15")  # ~first month's mid
    assert days.max() <= pd.Timestamp("2026-12-17")  # ~last month's mid


def test_spline_passes_through_a_settle() -> None:
    # Feb's mid-delivery is Feb 15 00:00 (a grid day), so the daily value there == Feb's settle.
    settles = [80.0, 90.0, 85.0, 95.0, 100.0, 105.0]
    df = eq.to_canonical(_raw(months=6, settle=settles, start="2026-01-01"))
    feb = df.loc[df["date"] == pd.Timestamp("2026-02-15"), "value"]
    assert len(feb) == 1
    assert feb.iloc[0] == pytest.approx(90.0, abs=1e-6)


def test_constant_strip_gives_constant_daily_curve() -> None:
    # A flat settle strip splines to a flat daily curve (natural cubic spline of constant data).
    df = eq.to_canonical(_raw(months=12, settle=100.0))
    assert df["value"].to_numpy() == pytest.approx(100.0, abs=1e-6)


def test_non_monthly_contracts_are_ignored() -> None:
    # A far-dated yearly contract must not extend the curve beyond the monthly strip.
    extra = [{"made_on": "2025-12-05", "period": "year", "delivery": "2031-01-01", "value": 120.0}]
    monthly_only = eq.to_canonical(_raw(months=6))
    with_year = eq.to_canonical(_raw(months=6, extra=extra))
    assert with_year["date"].max() == monthly_only["date"].max()


def test_multiple_vintages_of_same_day_coexist() -> None:
    a = eq.to_canonical(_raw(made_on="2025-12-04", settle=100.0))
    b = eq.to_canonical(_raw(made_on="2025-12-05", settle=101.0))
    df = pd.concat([a, b], ignore_index=True)
    schema.validate(df, lazy=True)  # same (date, series_id), different made_on -> passes


def test_single_month_vintage_is_skipped() -> None:
    assert eq.to_canonical(_raw(months=1)).empty  # <2 knots -> no spline


def test_empty_raw_is_empty() -> None:
    empty = pd.DataFrame(columns=["made_on", "period", "delivery", "value"])
    assert eq.to_canonical(empty).empty


def test_absurd_value_is_blocked() -> None:
    df = eq.to_canonical(_raw(settle=1e9))  # e.g. a unit/scale mistake
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
    assert eq._vintages_to_delete(made_ons, today) == {old_nonmon, old_mon_over_year}


def test_desired_run_dates_skips_weekends() -> None:
    today = dt.date(2026, 6, 25)  # Thursday
    desired = set(eq._desired_run_dates(today))
    assert all(d.weekday() < 5 for d in desired)  # no weekend trading date requested
    sat = dt.date(2026, 6, 20)
    assert sat.weekday() == 5 and sat not in desired
    assert today in desired
    assert min(desired) >= today - dt.timedelta(days=365)  # nothing older than a year
    assert max(desired) <= today  # nothing in the future
