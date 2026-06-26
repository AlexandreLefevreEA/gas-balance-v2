"""Shared Kpler HTTP retry helper — fixture-based, no live network.

One place to verify the 429/5xx-then-200 retry survives, for both the sync `request` and
the async `arequest`. (Previously this same test was copied into each Kpler connector's
test file.)
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx

from gasbalance_etl.connectors import _kpler_http


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {"ratelimit-reset": "0"}  # 0s backoff -> fast test

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return {"data": []}


class _FakeSyncClient:
    def __init__(self, statuses: list[int]) -> None:
        self._resps = [_FakeResp(s) for s in statuses]
        self.calls = 0

    def get(self, endpoint: str, params: Any = None) -> _FakeResp:
        r = self._resps[self.calls]
        self.calls += 1
        return r


class _FakeAsyncClient:
    def __init__(self, statuses: list[int]) -> None:
        self._resps = [_FakeResp(s) for s in statuses]
        self.calls = 0

    async def get(self, endpoint: str, params: Any = None) -> _FakeResp:
        r = self._resps[self.calls]
        self.calls += 1
        return r


def test_request_retries_transient_then_succeeds() -> None:
    # a 429 and a 502 must be retried (not abort the run); the run survives.
    client = _FakeSyncClient([429, 502, 200])
    resp = _kpler_http.request(cast(httpx.Client, client), "ep", {}, label="kpler-test")
    assert resp.status_code == 200
    assert client.calls == 3


def test_arequest_retries_transient_then_succeeds() -> None:
    client = _FakeAsyncClient([429, 502, 200])
    resp = asyncio.run(
        _kpler_http.arequest(cast(httpx.AsyncClient, client), "ep", {}, label="kpler-test")
    )
    assert resp.status_code == 200
    assert client.calls == 3
