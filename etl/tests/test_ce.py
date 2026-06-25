"""CE connector contract test — fixture-based, no live network, no DB.

Covers the multi-id CSV parser and the data-trust gate (non-finite values are
rejected). The shared compose primitive is tested in test_compose.py.
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
