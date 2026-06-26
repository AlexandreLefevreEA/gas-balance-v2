"""Kpler gas-spot connector contract test — fixture-based, no live network, no DB.

Covers the day-ahead 'DAY 1 MW' record selection (+ settlement->last fallback), the
market-area -> canonical mapping, dropping unknown areas / nulls, and the EUR/MWh band.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_gas_spot import connector as gs
from gasbalance_etl.validation.gas_spot import gas_spot_schema as schema

_TD = pd.Timestamp("2026-06-24T00:00:00")  # trading date, naive, as fetch() produces


def _raw(rows: list[tuple[str, float | None]]) -> pd.DataFrame:
    """Build a raw [market_area, date, value] frame (one trading day) like fetch() returns."""
    return pd.DataFrame(
        [{"market_area": ma, "date": _TD, "value": v} for ma, v in rows],
        columns=["market_area", "date", "value"],
    )


def _rec(
    tenor: str, long_name: str, settle: float | None, last: float | None = None
) -> dict[str, Any]:
    """One Kpler price product record (the fields `_day1_value` reads)."""
    return {"tenor": tenor, "longName": long_name, "settlementPrice": settle, "lastPrice": last}


def test_series_dict_one_series_per_hub() -> None:
    sd = gs.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.GASSPOT.TTF", "KP.GASSPOT.THE"} <= codes
    assert len(codes) == len(sd)  # exactly one series per hub, no dups
    assert {e["group"] for e in sd} == {"price"}
    assert {e["sub_group"] for e in sd} == {"gas_spot"}


def test_day1_value_picks_day_ahead_settlement() -> None:
    """Among the day's products, pick the day-ahead 'DAY 1 MW' settlement — not the weekend
    leg, the within-day, or the duplicate named spot-index root."""
    records = [
        _rec("within_day", "WITHIN-DAY", 99.0, 99.0),
        _rec("day_ahead", "SAT MW", None, None),
        _rec("day_ahead", "DAY 1 MW", 40.667, 40.6),
        _rec("day_ahead", "EEX TTF Natural Gas Day Spot", 40.7),
    ]
    assert gs._day1_value(records) == 40.667


def test_day1_value_falls_back_to_last_when_unsettled() -> None:
    records = [_rec("day_ahead", "GAS DAY 1 MW", None, 85.5)]
    assert gs._day1_value(records) == 85.5


def test_day1_value_none_when_absent() -> None:
    assert gs._day1_value([_rec("within_day", "WITHIN-DAY", 10.0, 10.0)]) is None
    assert gs._day1_value([]) is None


def test_to_canonical_maps_series() -> None:
    df = gs.to_canonical(_raw([("TTF", 40.667), ("THE", 41.122)]))
    by_id = dict(zip(df["series_id"], df["value"], strict=True))
    assert by_id["KP.GASSPOT.TTF"] == 40.667
    assert by_id["KP.GASSPOT.THE"] == 41.122
    assert (df["source"] == "kpler_gas_spot").all()
    assert (df["group"] == "price").all()
    schema.validate(df, lazy=True)  # daily trading-date ts + plausible EUR/MWh -> passes


def test_unknown_area_and_null_dropped() -> None:
    df = gs.to_canonical(
        _raw(
            [
                ("TTF", 40.0),  # the one valid row
                ("ZZ", 10.0),  # market area not in the dictionary -> dropped
                ("THE", None),  # null value -> dropped
            ]
        )
    )
    assert list(df["series_id"]) == ["KP.GASSPOT.TTF"]
    assert df.loc[0, "value"] == 40.0


def test_absurd_value_is_blocked() -> None:
    df = gs.to_canonical(_raw([("TTF", 1e7)]))  # e.g. a p/therm or x1000 scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
