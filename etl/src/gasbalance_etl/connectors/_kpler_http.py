"""Shared Kpler HTTP helpers — one 429/5xx retry/backoff path for every Kpler connector.

The Kpler connectors hit the same v2 API and retried transient failures identically. This is
that logic, once: `retry_after` (backoff seconds) plus `request` / `arequest` (the sync and async
GET-with-retry). Connectors pass their own `endpoint` and a `label` for the log line. A leaf module
(leading underscore = not a connector source), reused like `kpler_actual_temps.config`.

**Global in-flight cap.** Every Kpler (and EQ — same `arequest`) request passes through here, so the
process-wide `_inflight` semaphore bounds *total* concurrent requests across all connectors and all
`etl run all` worker threads. A connector's own `_CONCURRENCY` is then just a local cap; the global
one (env `KPLER_MAX_INFLIGHT`, default 12) is what keeps parallelism from amplifying 429s. The slot
is held only around the in-flight GET and released during backoff, so a throttled call frees it for
others. `threading.BoundedSemaphore` (not `asyncio`) because the cap must hold across threads/loops.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Process-wide cap on concurrent Kpler/EQ requests (see module docstring). One knob to match the
# API's real quota; raise if Kpler tolerates more, lower if 429s persist.
_inflight = threading.BoundedSemaphore(int(os.environ.get("KPLER_MAX_INFLIGHT", "12")))


def retry_after(resp: httpx.Response, attempt: int) -> float:
    """Seconds to back off before a retry, by failure kind.

    429 (rate-limited): honour `retry-after` / `ratelimit-reset`, capped at 20s (a real rate window
    rarely needs longer, and a 58s server hint stalls the whole run). 5xx (gateway, *not* a rate
    window): a short exponential only — no point waiting on a rate reset that isn't happening.
    """
    if resp.status_code == 429:
        raw = resp.headers.get("retry-after") or resp.headers.get("ratelimit-reset")
        try:
            secs = float(raw) if raw else 2.0**attempt
        except ValueError:  # retry-after as an HTTP-date — just back off
            secs = 2.0**attempt
        return min(secs, 20.0)
    return min(2.0**attempt, 8.0)  # 5xx


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
        with _inflight:  # hold a global slot only for the in-flight request
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
        time.sleep(wait)  # backoff without holding a slot
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
        # Acquire a global slot without blocking the event loop; poll briefly when at the cap.
        while not _inflight.acquire(blocking=False):
            await asyncio.sleep(0.05)
        try:
            resp = await client.get(endpoint, params=params)
        finally:
            _inflight.release()
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
        await asyncio.sleep(wait)  # backoff without holding a slot
    assert resp is not None
    resp.raise_for_status()  # retries exhausted -> surface the last transient error
    return resp
