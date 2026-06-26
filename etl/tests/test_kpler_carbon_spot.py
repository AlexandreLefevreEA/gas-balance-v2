"""Kpler carbon-spot connector contract test — fixture-based, no live network, no DB.

Covers the single-series mapping, keeping SEME (EUA spot) over SEMA (EUAA aviation allowance),
dropping null settlements, and the EUR/tCO2 sanity band (an absurd value is blocked).
"""

from __future__ import annotations

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_carbon_spot import connector as carbon
from gasbalance_etl.validation.carbon import carbon_schema as schema

_TS = pd.Timestamp("2026-06-24T00:00:00")  # naive UTC midnight, as fetch() produces


def _raw(rows: list[tuple[str, float | None]]) -> pd.DataFrame:
    """Build a raw [date, root, value] frame (one trading day) like fetch() returns."""
    return pd.DataFrame(
        [{"date": _TS, "root": r, "value": v} for r, v in rows],
        columns=["date", "root", "value"],
    )


def test_series_dict_single_carbon_series() -> None:
    sd = carbon.series_dict()
    assert len(sd) == 1
    assert sd[0]["code"] == "KP.CARBON.SPOT"
    assert sd[0]["group"] == "carbon"
    assert sd[0]["unit"] == "EUR/tCO2"


def test_to_canonical_keeps_seme_settlement() -> None:
    df = carbon.to_canonical(_raw([("SEME", 79.7)]))
    assert list(df["series_id"]) == ["KP.CARBON.SPOT"]
    assert df["value"].iloc[0] == 79.7
    assert (df["source"] == "kpler_carbon_spot").all()
    schema.validate(df, lazy=True)  # midnight ts + plausible EUA price -> passes


def test_sema_and_null_dropped() -> None:
    # SEMA (EUAA aviation allowance) excluded; null settlement dropped; only SEME kept.
    df = carbon.to_canonical(_raw([("SEME", 79.7), ("SEMA", 79.5), ("SEME", None)]))
    assert list(df["series_id"]) == ["KP.CARBON.SPOT"]
    assert list(df["value"]) == [79.7]


def test_absurd_value_is_blocked() -> None:
    df = carbon.to_canonical(_raw([("SEME", 1e6)]))  # e.g. a scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
