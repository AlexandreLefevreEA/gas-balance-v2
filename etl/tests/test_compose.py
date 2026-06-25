"""Shared compose primitive + derived selector resolution — pure, no DB/network.

Covers sum(positive)-sum(negative) composition, referenced_ids, group/sub_group
selection, selector resolution, and an end-to-end derived balance (resolve ->
compose -> identity check) mirroring settings/derived.yaml.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from gasbalance_etl.transforms.compose import compose, referenced_ids, resolve, select_codes
from gasbalance_etl.validation.canonical import canonical_schema as schema
from gasbalance_etl.validation.identities import check_identities


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
            "positive": ["55306"],
            "negative": ["55259"],
        }
    ]
    df = compose(entries, _wide(), "ce")
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
    entries = [{"code": "CE.Y", "name": "Y", "positive": [], "negative": ["55259"]}]
    df = compose(entries, _wide(), "ce")
    assert df["value"].tolist() == [-3.0, -4.0]


def test_referenced_ids_unique_order_preserving() -> None:
    entries = [
        {"code": "A", "positive": ["1", "2"], "negative": ["3"]},
        {"code": "B", "positive": ["2"], "negative": ["4"]},
    ]
    assert referenced_ids(entries) == ["1", "2", "3", "4"]


# --- derived selection (group/sub_group) ------------------------------------

_CATALOG: dict[str, dict[str, Any]] = {
    "P1": {"group": "production", "sub_group": None, "area": "DE"},
    "P2": {"group": "production", "sub_group": None, "area": "FR"},
    "D1": {"group": "demand", "sub_group": None, "area": "DE"},
    "W1": {"group": "storage", "sub_group": "withdrawal", "area": "DE"},
    "L1": {"group": "storage", "sub_group": "level", "area": "DE"},
    "LNG1": {"group": "lng", "sub_group": None, "area": "DE"},
    "LNGL": {"group": "lng", "sub_group": "level", "area": "DE"},
}


def test_select_codes_by_group_subgroup_area() -> None:
    assert select_codes(_CATALOG, [{"group": "production"}]) == ["P1", "P2"]
    # explicit null sub_group matches NULL only (LNG sendout, not level)
    assert select_codes(_CATALOG, [{"group": "lng", "sub_group": None}]) == ["LNG1"]
    assert select_codes(_CATALOG, [{"group": "storage", "sub_group": "withdrawal"}]) == ["W1"]
    assert select_codes(_CATALOG, [{"group": "production", "area": "DE"}]) == ["P1"]
    assert select_codes(_CATALOG, []) == []


def test_resolve_expands_selectors() -> None:
    entries = [{"code": "EU.SUPPLY", "positive_select": [{"group": "production"}]}]
    out = resolve(entries, _CATALOG)
    assert out[0]["positive"] == ["P1", "P2"]
    assert out[0]["negative"] == []


def _balance_entries() -> list[dict[str, Any]]:
    return [
        {
            "code": "EU.SUPPLY",
            "name": "EU Supply",
            "group": "balance",
            "positive_select": [{"group": "production"}, {"group": "lng", "sub_group": None}],
        },
        {
            "code": "EU.BALANCE",
            "name": "EU Balance",
            "group": "balance",
            "check": "zero_sum",
            "tolerance": 0.5,
            "positive_select": [{"group": "production"}, {"group": "lng", "sub_group": None}],
            "negative_select": [
                {"group": "demand"},
                {"group": "storage", "sub_group": "withdrawal"},
            ],
        },
    ]


def _balance_wide(withdrawal: float) -> pd.DataFrame:
    idx = pd.to_datetime([dt.date(2024, 1, 1), dt.date(2024, 1, 2)])
    return pd.DataFrame(
        {
            "P1": [10.0, 10.0],
            "P2": [5.0, 5.0],
            "LNG1": [2.0, 2.0],
            "D1": [15.0, 15.0],
            "W1": [withdrawal, withdrawal],
            "L1": [100.0, 100.0],  # storage level — must NOT enter the balance
            "LNGL": [50.0, 50.0],  # lng level — must NOT enter supply
        },
        index=idx,
    )


def test_derived_balance_end_to_end() -> None:
    resolved = resolve(_balance_entries(), _CATALOG)
    df = compose(resolved, _balance_wide(withdrawal=2.0), "derived")
    supply = df.loc[df["series_id"] == "EU.SUPPLY", "value"].tolist()
    balance = df.loc[df["series_id"] == "EU.BALANCE", "value"].tolist()
    assert supply == [17.0, 17.0]  # P1+P2+LNG1, excludes LNGL/L1
    assert balance == [0.0, 0.0]  # 17 - (D1=15 + W1=2)
    check_identities(df, resolved)  # residual within tolerance -> no raise


def test_derived_balance_identity_breach_blocks() -> None:
    resolved = resolve(_balance_entries(), _CATALOG)
    df = compose(resolved, _balance_wide(withdrawal=5.0), "derived")  # balance = -3
    with pytest.raises(ValueError, match=r"EU\.BALANCE"):
        check_identities(df, resolved)
