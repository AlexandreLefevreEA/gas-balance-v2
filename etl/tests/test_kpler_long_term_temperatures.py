"""Kpler long-term temperature connector contract test — fixture-based, no live network, no DB.

Covers the dynamic model list (MEAN + last-10 REF years), the (zone, model)→canonical
mapping, the unknown zone/model drop, the temperature-range gate, and the 429/5xx retry.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import httpx
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


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {"ratelimit-reset": "0"}  # 0s backoff -> fast test

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return {"data": []}


class _FakeClient:
    def __init__(self, statuses: list[int]) -> None:
        self._resps = [_FakeResp(s) for s in statuses]
        self.calls = 0

    def get(self, endpoint: str, params: Any = None) -> _FakeResp:
        r = self._resps[self.calls]
        self.calls += 1
        return r


def test_request_retries_transient_then_succeeds() -> None:
    # a 429 and a 502 must be retried (not abort the run); the run survives.
    client = _FakeClient([429, 502, 200])
    resp = kp._request(cast(httpx.Client, client), {})
    assert resp.status_code == 200
    assert client.calls == 3
