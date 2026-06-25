"""Kpler temperature connector contract test — fixture-based, no live network, no DB.

Covers the day-ahead (D-1) slice extraction (date filter + null drop), the zone→canonical
mapping, and the temperature-range gate (an absurd value is rejected by the schema).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, cast

import httpx
import pandas as pd
import pandera.errors as pa_errors
import pytest

from gasbalance_etl.connectors.kpler_actual_temps import connector as kp
from gasbalance_etl.validation.kpler_actual_temps import temperature_schema as schema

# A 00z run on 2022-07-10 forecasting forward; only the next day (D-1) is the actual proxy.
_RUN = dt.date(2022, 7, 10)
_DATA: list[dict[str, Any]] = [
    {"zone": "FR", "startDate": "2022-07-10T23:00:00+00:00", "value": 18.0},  # run day -> excluded
    {"zone": "FR", "startDate": "2022-07-11T00:00:00+00:00", "value": 17.0},  # day-ahead -> kept
    {"zone": "FR", "startDate": "2022-07-11T01:00:00+00:00", "value": None},  # null -> dropped
    {"zone": "DE", "startDate": "2022-07-11T00:00:00+00:00", "value": 16.0},  # day-ahead (DE)
    {"zone": "FR", "startDate": "2022-07-12T00:00:00+00:00", "value": 20.0},  # D-2 -> excluded
]


def test_day_ahead_rows_keeps_next_day_nonnull() -> None:
    rows = kp._day_ahead_rows(_RUN, _DATA)
    assert rows == [
        ("FR", "2022-07-11T00:00:00+00:00", 17.0),
        ("DE", "2022-07-11T00:00:00+00:00", 16.0),
    ]


def _raw(zone: str = "FR", value: float = 17.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"zone": [zone], "date": pd.to_datetime(["2022-07-11T00:00:00"]), "value": [value]}
    )


def test_to_canonical_maps_zone_to_series() -> None:
    df = kp.to_canonical(_raw("FR", 17.0))
    assert df.loc[0, "series_id"] == "KP.TEMP.FR"
    assert df.loc[0, "area"] == "FR"
    assert df.loc[0, "group"] == "temperature"
    assert (df["source"] == "kpler_actual_temps").all()
    schema.validate(df, lazy=True)  # hourly timestamp + plausible value -> passes


def test_unknown_zone_is_dropped() -> None:
    # a zone not in the dictionary maps to nothing (inner join)
    assert kp.to_canonical(_raw("ZZ", 17.0)).empty


def test_absurd_temperature_is_blocked() -> None:
    df = kp.to_canonical(_raw("FR", 999.0))  # e.g. a Kelvin mistake
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

    async def get(self, endpoint: str, params: Any = None) -> _FakeResp:
        r = self._resps[self.calls]
        self.calls += 1
        return r


def test_request_retries_transient_then_succeeds() -> None:
    # a 429 and a 502 must be retried (not abort the backfill); the run survives.
    client = _FakeClient([429, 502, 200])
    resp = asyncio.run(kp._request(cast(httpx.AsyncClient, client), {}))
    assert resp.status_code == 200
    assert client.calls == 3
