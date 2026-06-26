"""Shared Kpler HTTP helpers — one 429/5xx retry/backoff path for every Kpler connector.

The three Kpler connectors (actual temps, long-term, forecast) hit the same v2 API and
retried transient failures identically. This is that logic, once: `retry_after` (backoff
seconds) plus `request` / `arequest` (the sync and async GET-with-retry). Connectors pass
their own `endpoint` and a `label` for the log line. A leaf module (leading underscore =
not a connector source), reused like `kpler_actual_temps.config.get_kpler_settings`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


def retry_after(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying — honour retry-after / ratelimit-reset, else backoff."""
    raw = resp.headers.get("retry-after") or resp.headers.get("ratelimit-reset")
    try:
        secs = float(raw) if raw else 2.0**attempt
    except ValueError:  # retry-after as an HTTP-date — just back off
        secs = 2.0**attempt
    return min(secs, 60.0)


def request(
    client: httpx.Client,
    endpoint: str,
    params: dict[str, Any],
    *,
    label: str,
    max_retries: int = 6,
) -> httpx.Response:
    """Sync GET, retrying 429/5xx with backoff; raises (fails loudly) only once retries run out."""
    resp = None
    for attempt in range(max_retries):
        resp = client.get(endpoint, params=params)
        if resp.status_code != 429 and resp.status_code < 500:
            resp.raise_for_status()
            return resp
        wait = retry_after(resp, attempt)
        log.warning(
            "%s: HTTP %d; backing off %.0fs (attempt %d/%d)",
            label,
            resp.status_code,
            wait,
            attempt + 1,
            max_retries,
        )
        time.sleep(wait)
    assert resp is not None
    resp.raise_for_status()  # retries exhausted -> surface the last transient error
    return resp


async def arequest(
    client: httpx.AsyncClient,
    endpoint: str,
    params: dict[str, Any],
    *,
    label: str,
    max_retries: int = 6,
) -> httpx.Response:
    """Async GET, retrying 429/5xx with backoff; raises (fails loudly) only once retries run out."""
    resp = None
    for attempt in range(max_retries):
        resp = await client.get(endpoint, params=params)
        if resp.status_code != 429 and resp.status_code < 500:
            resp.raise_for_status()
            return resp
        wait = retry_after(resp, attempt)
        log.warning(
            "%s: HTTP %d; backing off %.0fs (attempt %d/%d)",
            label,
            resp.status_code,
            wait,
            attempt + 1,
            max_retries,
        )
        await asyncio.sleep(wait)
    assert resp is not None
    resp.raise_for_status()  # retries exhausted -> surface the last transient error
    return resp
