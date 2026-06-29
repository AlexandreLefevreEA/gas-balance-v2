"""eq_coal_spot connector contract test — fixture-based, no live network, no DB.

Covers the front-month filter (period=MONTH, front=1), the settlement→close fallback, the
holiday duplicate-trading-day dedupe, single-series mapping, and the USD/t sanity band.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.eq_coal_spot import connector as coal
from gasbalance_etl.validation.coal_spot import coal_spot_schema as schema

_COLS = ["traded", "period", "front", "settlement", "close"]


def _row(**kw: Any) -> dict[str, Any]:
    """One OHLC entry (post-fetch shape) — front-month month/1 by default; override per test.

    EQ returns `period` lowercased ("month"/"year"/…), matching what fetch() extracts.
    """
    base: dict[str, Any] = {
        "traded": "2025-12-05",
        "period": "month",
        "front": 1,
        "settlement": None,
        "close": None,
    }
    base.update(kw)
    return base


def _raw(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a raw [traded, period, front, settlement, close] frame like fetch() returns."""
    return pd.DataFrame(rows, columns=_COLS)


def test_series_dict_single_coal_series() -> None:
    sd = coal.series_dict()
    assert len(sd) == 1
    assert sd[0]["code"] == "EQ.COAL.API2"
    assert sd[0]["group"] == "price"
    assert sd[0]["sub_group"] == "coal"
    assert sd[0]["unit"] == "USD/t"


def test_to_canonical_keeps_front_month_settlement() -> None:
    # Front-month MONTH/1 with a settlement; the MONTH/2 and YEAR/1 contracts must be dropped.
    df = coal.to_canonical(
        _raw(
            [
                _row(settlement=105.25, close=105.0),
                _row(front=2, settlement=110.0, close=109.5),
                _row(period="year", settlement=120.0, close=119.0),
            ]
        )
    )
    assert list(df["series_id"]) == ["EQ.COAL.API2"]
    assert df["value"].iloc[0] == 105.25
    assert df["date"].iloc[0] == pd.Timestamp("2025-12-05")
    assert (df["source"] == "eq_coal_spot").all()
    assert (df["group"] == "price").all()
    schema.validate(df, lazy=True)  # naive ts + plausible USD/t -> passes


def test_settlement_falls_back_to_close() -> None:
    # No settlement on the front month -> use close.
    df = coal.to_canonical(_raw([_row(settlement=None, close=104.0)]))
    assert list(df["value"]) == [104.0]


def test_null_value_dropped() -> None:
    # Front month with neither settlement nor close -> dropped, empty canonical frame.
    df = coal.to_canonical(_raw([_row(settlement=None, close=None)]))
    assert df.empty


def test_holiday_duplicate_trading_day_deduped() -> None:
    # /latest/ around a holiday repeats the prior trading day; dedupe to one (date, series_id).
    df = coal.to_canonical(
        _raw([_row(settlement=105.25, close=105.0), _row(settlement=105.25, close=105.0)])
    )
    assert len(df) == 1
    schema.validate(df, lazy=True)


def test_empty_raw_returns_empty_canonical() -> None:
    df = coal.to_canonical(_raw([]))
    assert df.empty
    assert list(df.columns) == [
        "date",
        "series_id",
        "name",
        "group",
        "sub_group",
        "area",
        "value",
        "source",
    ]


def test_absurd_value_is_blocked() -> None:
    df = coal.to_canonical(_raw([_row(settlement=1e6)]))  # e.g. a scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
