"""CE connector contract test — fixture-based, no live network, no DB.

Covers the multi-id CSV parser, the compose logic (sum positive - sum negative,
incl. pure-negative), and the data-trust gate (non-finite values are rejected).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.ce import connector as ce
from gasbalance_etl.validation.canonical import canonical_schema as schema


def test_parse_multi_csv() -> None:
    # CE eugasseries CSV: DateExcel, Date, then one column per requested id.
    csv_text = (
        "DateExcel,Date,55306,55259\n"
        "41640,01-Jan-2014,10.0,3.0\n"
        "41641,02-Jan-2014,20.0,\n"  # 55259 missing -> dropped from that series
    )
    out = ce._parse_multi(csv_text)
    assert set(out) == {"55306", "55259"}
    assert out["55306"].loc[dt.date(2014, 1, 1)] == 10.0
    assert dt.date(2014, 1, 2) not in out["55259"].index


def _wide() -> pd.DataFrame:
    idx = pd.to_datetime([dt.date(2014, 1, 1), dt.date(2014, 1, 2)])
    return pd.DataFrame({"55306": [10.0, 20.0], "55259": [3.0, 4.0]}, index=idx)


def test_compose_positive_minus_negative() -> None:
    entries = [
        {
            "code": "CE.X",
            "name": "X",
            "group": "storage",
            "sub_group": None,
            "area": "HR",
            "unit": "mcm",
            "positive": ["55306"],
            "negative": ["55259"],
        }
    ]
    df = ce._compose(entries, _wide())
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
    assert df["value"].tolist() == [7.0, 16.0]  # 10-3, 20-4
    assert (df["source"] == "ce").all()
    schema.validate(df, lazy=True)


def test_compose_pure_negative() -> None:
    entries = [
        {
            "code": "CE.Y",
            "name": "Y",
            "group": "storage",
            "sub_group": "withdrawal",
            "area": "HR",
            "unit": "mcm",
            "positive": [],
            "negative": ["55259"],
        }
    ]
    df = ce._compose(entries, _wide())
    assert df["value"].tolist() == [-3.0, -4.0]


def test_nonfinite_value_is_blocked() -> None:
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([dt.date(2014, 1, 1)]),
            "series_id": ["CE.X"],
            "name": ["X"],
            "group": ["storage"],
            "sub_group": [None],
            "area": ["HR"],
            "value": [float("inf")],
            "source": ["ce"],
        }
    )
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
