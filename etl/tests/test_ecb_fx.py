"""ECB FX connector contract test — fixture-based, no live network, no DB.

Covers the wide-CSV parse (trailing `Unnamed` column, `N/A`/blank dropping, currency filter),
the currency -> canonical mapping, dropping unknown currencies / nulls, and the EUR band.
"""

from __future__ import annotations

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.ecb_fx import connector as ecb
from gasbalance_etl.validation.fx import fx_schema as schema

_D = pd.Timestamp("2026-06-24T00:00:00")  # a rate date, naive, as fetch() produces

# A miniature ECB hist CSV: header ends with a comma (-> a trailing `Unnamed` column), an
# unwanted currency (JPY), and a row mixing `N/A` and a blank cell.
_CSV = (
    "Date,USD,JPY,GBP,NOK,\n"
    "2026-06-24,1.0732,172.50,0.8536,11.45,\n"
    "2026-06-23,N/A,171.80,0.8540,,\n"
)


def _raw(rows: list[tuple[str, float | None]]) -> pd.DataFrame:
    """Build a raw [date, currency, value] frame (one date) like fetch() returns."""
    return pd.DataFrame(
        [{"date": _D, "currency": ccy, "value": v} for ccy, v in rows],
        columns=["date", "currency", "value"],
    )


def test_parse_keeps_wanted_drops_unnamed_and_na() -> None:
    out = ecb._parse(_CSV, {"USD", "GBP", "NOK"})
    # JPY (unwanted) and the trailing Unnamed column are gone; N/A USD + blank NOK are dropped.
    assert set(out["currency"]) == {"USD", "GBP", "NOK"}
    assert len(out) == 4  # 24th: USD,GBP,NOK (3) + 23rd: GBP only (1)
    assert pd.api.types.is_datetime64_any_dtype(out["date"])  # canonical schema coerces to ns
    usd_24 = out[(out["currency"] == "USD") & (out["date"] == _D)]["value"]
    assert usd_24.item() == 1.0732
    # the N/A USD cell on the 23rd produced no row
    assert not ((out["currency"] == "USD") & (out["date"] == pd.Timestamp("2026-06-23"))).any()


def test_series_dict_one_series_per_currency() -> None:
    sd = ecb.series_dict()
    codes = {e["code"] for e in sd}
    assert codes == {"ECB.FX.USD", "ECB.FX.GBP", "ECB.FX.NOK"}
    assert len(codes) == len(sd)  # exactly one series per currency, no dups
    assert {e["group"] for e in sd} == {"fx"}
    assert {e["sub_group"] for e in sd} == {"spot"}


def test_to_canonical_maps_series() -> None:
    df = ecb.to_canonical(_raw([("USD", 1.0732), ("GBP", 0.8536)]))
    by_id = dict(zip(df["series_id"], df["value"], strict=True))
    assert by_id["ECB.FX.USD"] == 1.0732
    assert by_id["ECB.FX.GBP"] == 0.8536
    assert (df["source"] == "ecb_fx").all()
    assert (df["group"] == "fx").all()
    schema.validate(df, lazy=True)  # daily ts + plausible foreign-per-EUR -> passes


def test_unknown_currency_and_null_dropped() -> None:
    df = ecb.to_canonical(
        _raw(
            [
                ("USD", 1.0732),  # the one valid row
                ("ZZZ", 5.0),  # currency not in the dictionary -> dropped
                ("GBP", None),  # null value -> dropped
            ]
        )
    )
    assert list(df["series_id"]) == ["ECB.FX.USD"]
    assert df.iloc[0]["value"] == 1.0732


def test_absurd_value_is_blocked() -> None:
    df = ecb.to_canonical(_raw([("USD", 1e7)]))  # e.g. a scale/corruption mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)
