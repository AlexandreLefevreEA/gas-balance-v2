"""Kpler long-term temperature connector contract test — fixture-based, no live network, no DB.

Covers the dynamic model list (MEAN + last-10 REF years), the (zone, model)→canonical
mapping, the unknown zone/model drop, and the temperature-range gate. (Retry/backoff is
shared and tested in test_kpler_http.py.)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_long_term_temperatures import connector as kp
from gasbalance_etl.validation.temperature import temperature_schema as schema


def test_models_is_mean_plus_last_ten_ref_years() -> None:
    models = kp._models()
    y = dt.date.today().year
    assert models[0] == "MEAN"
    assert len(models) == 11
    assert models[1:] == [f"REF_{yr}" for yr in range(y - 10, y)]


def test_code_strips_ref_underscore() -> None:
    assert kp._code("FR", "MEAN") == "KP.TEMPLT.FR.MEAN"
    assert kp._code("FR", "REF_2020") == "KP.TEMPLT.FR.REF2020"


def _raw(zone: str = "FR", model: str = "MEAN", value: float = 17.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "zone": [zone],
            "model": [model],
            "date": pd.to_datetime(["2026-07-01T00:00:00"]),
            "value": [value],
        }
    )


def test_to_canonical_maps_zone_model_to_series() -> None:
    df = kp.to_canonical(_raw("FR", "MEAN", 17.0))
    assert df.loc[0, "series_id"] == "KP.TEMPLT.FR.MEAN"
    assert df.loc[0, "sub_group"] == "MEAN"
    assert df.loc[0, "area"] == "FR"
    assert df.loc[0, "group"] == "temperature_longterm"
    assert (df["source"] == "kpler_long_term_temperatures").all()
    schema.validate(df, lazy=True)  # hourly timestamp + plausible value -> passes


def test_unknown_zone_is_dropped() -> None:
    # a zone not in the dictionary maps to nothing (inner join)
    assert kp.to_canonical(_raw("ZZ", "MEAN", 17.0)).empty


def test_unknown_model_is_dropped() -> None:
    # a model not in the current dictionary (e.g. out-of-window REF year) drops out
    assert kp.to_canonical(_raw("FR", "REF_1700", 17.0)).empty


def test_absurd_temperature_is_blocked() -> None:
    df = kp.to_canonical(_raw("FR", "MEAN", 999.0))  # e.g. a Kelvin mistake
    with pytest.raises(pa_errors.SchemaErrors):
        schema.validate(df, lazy=True)


def test_fetch_skips_when_recently_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    # Full refresh is weekly: a recent covariate load -> fetch returns empty without any network.
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    monkeypatch.setattr(kp, "last_loaded_at", lambda source: recent)
    df = kp.fetch(None)
    assert df.empty
    assert list(df.columns) == ["zone", "model", "date", "value"]
