"""carbon_curve transform contract test — fixture-based, no DB, no network.

Covers the pure spline (`_spline_curve`): interpolates exactly through its anchors, samples a
contiguous daily grid, drops maturities before the trading date (spot stays the near anchor), and
needs >=2 nodes. Plus `to_canonical`: groups the anchors frame by `made_on`, splines each vintage
into a daily curve starting at the spot, and passes `forecast_covariate_carbon_schema`.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from gasbalance_etl.transforms import carbon_curve as cc
from gasbalance_etl.validation.forecast_covariate import (
    forecast_covariate_carbon_schema as schema,
)

_MADE_ON = dt.date(2026, 6, 25)


def _raw(
    made_on: str = "2026-06-25",
    spot: float = 80.0,
    settles: tuple[tuple[str, float], ...] = (
        ("2026-07-01", 80.6),
        ("2026-12-01", 83.0),
        ("2027-12-01", 88.0),
    ),
) -> pd.DataFrame:
    """Anchors frame as fetch() returns it: a spot row (anchor_date==made_on) + settlement rows."""
    rows = [{"made_on": pd.Timestamp(made_on), "anchor_date": pd.Timestamp(made_on), "value": spot}]
    rows += [
        {"made_on": pd.Timestamp(made_on), "anchor_date": pd.Timestamp(m), "value": v}
        for m, v in settles
    ]
    return pd.DataFrame(rows, columns=["made_on", "anchor_date", "value"])


def test_series_dict_single_derived_curve_series() -> None:
    sd = cc.series_dict()
    assert len(sd) == 1
    assert sd[0]["code"] == "KP.CARBON.CURVE"
    assert sd[0]["group"] == "carbon"
    assert sd[0]["sub_group"] == "eua_curve"
    assert sd[0]["unit"] == "EUR/tCO2"
    assert sd[0]["is_derived"] is True


def test_spline_interpolates_anchors_on_a_daily_grid() -> None:
    anchors = [
        (_MADE_ON, 80.0),
        (dt.date(2026, 7, 25), 81.5),
        (dt.date(2026, 12, 1), 83.0),
        (dt.date(2027, 12, 1), 88.0),
    ]
    curve = cc._spline_curve(_MADE_ON, anchors)
    by_date = {d: v for d, v in curve}

    for d, v in anchors:  # a spline passes exactly through its nodes
        assert by_date[d] == pytest.approx(v, abs=1e-6)

    dates = [d for d, _ in curve]
    assert dates[0] == _MADE_ON  # starts at the spot
    assert dates[-1] == dt.date(2027, 12, 1)  # ends at the last contract
    assert len(dates) == (dates[-1] - dates[0]).days + 1  # contiguous
    assert all((dates[i] - dates[i - 1]).days == 1 for i in range(1, len(dates)))  # daily


def test_spline_drops_maturities_before_the_trading_date() -> None:
    anchors = [
        (_MADE_ON, 80.0),
        (dt.date(2026, 6, 1), 79.0),  # front contract maturity < trading date -> dropped
        (dt.date(2026, 12, 1), 83.0),
    ]
    curve = cc._spline_curve(_MADE_ON, anchors)
    assert curve[0][0] == _MADE_ON  # spot is the near anchor, not the expired contract
    assert curve[-1][0] == dt.date(2026, 12, 1)
    assert curve[0][1] == pytest.approx(80.0, abs=1e-6)


def test_spline_needs_at_least_two_nodes() -> None:
    assert cc._spline_curve(_MADE_ON, [(_MADE_ON, 80.0)]) == []  # spot only
    assert cc._spline_curve(_MADE_ON, []) == []


def test_to_canonical_daily_curve_starts_at_spot_and_validates() -> None:
    df = cc.to_canonical(_raw())
    assert (df["series_id"] == "KP.CARBON.CURVE").all()
    assert (df["source"] == "carbon_curve").all()

    df = df.sort_values("date").reset_index(drop=True)
    assert df["date"].iloc[0] == pd.Timestamp("2026-06-25")  # near point = spot date
    assert df["value"].iloc[0] == pytest.approx(80.0, abs=1e-6)  # ... at the spot value
    assert df["date"].iloc[-1] == pd.Timestamp("2027-12-01")
    schema.validate(df, lazy=True)


def test_to_canonical_multiple_vintages_coexist() -> None:
    a = cc.to_canonical(_raw(made_on="2026-06-23", spot=79.5))
    b = cc.to_canonical(_raw(made_on="2026-06-24", spot=80.0))
    df = pd.concat([a, b], ignore_index=True)
    assert set(df["made_on"]) == {pd.Timestamp("2026-06-23"), pd.Timestamp("2026-06-24")}
    schema.validate(df, lazy=True)  # keyed on (made_on, date, series_id)


def test_to_canonical_skips_spot_only_vintages() -> None:
    # A trading date with a spot but no usable forward maturity -> no curve, but valid ones survive.
    spot_only = pd.DataFrame(
        [
            {
                "made_on": pd.Timestamp("2026-06-20"),
                "anchor_date": pd.Timestamp("2026-06-20"),
                "value": 79.0,
            }
        ],
        columns=["made_on", "anchor_date", "value"],
    )
    raw = pd.concat([spot_only, _raw()], ignore_index=True)
    df = cc.to_canonical(raw)
    assert set(df["made_on"]) == {pd.Timestamp("2026-06-25")}  # only the splined vintage remains


def test_to_canonical_empty_in_empty_out() -> None:
    empty = pd.DataFrame(columns=["made_on", "anchor_date", "value"])
    assert cc.to_canonical(empty).empty
