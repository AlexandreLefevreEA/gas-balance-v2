"""Kpler long-term power-demand connector contract test — fixture-based, no live network, no DB.

Covers the dynamic model list (MEAN + last-10 REF years), the (zone, model)→canonical mapping,
the unknown zone/model drop, and the MW sanity band. (Retry/backoff is shared and tested in
test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_power_demand_long_term import connector as kp
from gasbalance_etl.validation.demand import demand_schema as schema


def test_models_is_mean_plus_last_ten_ref_years() -> None:
    models = kp._models()
    y = dt.date.today().year
    assert models[0] == "MEAN"
    assert len(models) == 11
    assert models[1:] == [f"REF_{yr}" for yr in range(y - 10, y)]


def test_code_strips_ref_underscore() -> None:
    assert kp._code("FR", "MEAN") == "KP.LOADLT.FR.MEAN"
    assert kp._code("FR", "REF_2020") == "KP.LOADLT.FR.REF2020"


def test_series_dict_one_demand_series_per_zone_model() -> None:
    sd = kp.series_dict()
    codes = {e["code"] for e in sd}
    assert {"KP.LOADLT.FR.MEAN", "KP.LOADLT.DE.REF2020"} <= codes
    assert len(codes) == len(sd)  # no dups
    assert len(sd) % 11 == 0  # exactly 11 models per zone
    assert {e["group"] for e in sd} == {"demand_longterm"}
    assert {e["sub_group"] for e in sd} == {"demand"}


def _raw(zone: str = "FR", model: str = "MEAN", value: float = 40000.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "zone": [zone],
            "model": [model],
            "date": pd.to_datetime(["2026-07-01T00:00:00"]),
            "value": [value],
        }
    )


def test_to_canonical_maps_zone_model_to_series() -> None:
    df = kp.to_canonical(_raw("FR", "MEAN", 40000.0))
    assert df.loc[0, "series_id"] == "KP.LOADLT.FR.MEAN"
    assert df.loc[0, "sub_group"] == "demand"
    assert df.loc[0, "area"] == "FR"
    assert df.loc[0, "group"] == "demand_longterm"
    assert (df["source"] == "kpler_power_demand_long_term").all()
    schema.validate(df, lazy=True)  # hourly timestamp + plausible MW -> passes


def test_unknown_zone_is_dropped() -> None:
    # a zone not in the dictionary maps to nothing (inner join)
    assert kp.to_canonical(_raw("ZZ", "MEAN", 40000.0)).empty


def test_unknown_model_is_dropped() -> None:
    # a model not in the current dictionary (e.g. out-of-window REF year) drops out
    assert kp.to_canonical(_raw("FR", "REF_1700", 40000.0)).empty


def test_small_negative_is_allowed() -> None:
    # Kpler's feed can carry small metering-noise negatives; the MW band must not reject them.
    schema.validate(kp.to_canonical(_raw("FR", "MEAN", -1.0)), lazy=True)


def test_absurd_value_is_blocked() -> None:
    df = kp.to_canonical(_raw("FR", "MEAN", 1e9))  # e.g. a W-not-MW scale mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)


def test_fetch_skips_when_recently_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    # Full refresh is weekly: a recent covariate load -> fetch returns empty without any network.
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    monkeypatch.setattr(kp, "last_loaded_at", lambda source: recent)
    df = kp.fetch(None)
    assert df.empty
    assert list(df.columns) == ["zone", "model", "date", "value"]
