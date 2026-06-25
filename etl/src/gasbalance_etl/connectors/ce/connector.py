"""Commodity Essentials connector.

Auth: HTTP Basic Auth (CE_USERNAME / CE_PASSWORD). Format: CSV. Refresh: FULL — re-fetch
full history since 2014 every run; `since` is ignored (idempotent upsert makes re-runs safe).

Speed: `eugasseries` accepts comma-separated ids, so the ~258 raw series this connector
needs are fetched in a few **batched** requests (not one-per-series), run **async**
(`httpx.AsyncClient` + asyncio.gather, bounded). gzip is negotiated automatically. The
`…bulk` endpoints are 14-day-capped, so they suit incremental, not a since-2014 backfill.

Each v2 series is composed from raw CE seriesIds: value = sum(positive) - sum(negative),
aligned by date (skipna=False), per `settings/ce.yaml` (ported from legacy). Cross-column
balances are computed downstream by the derived stage (`transforms/derived.py`, ADR 0007).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
from typing import Any

import httpx
import pandas as pd

from gasbalance_etl.connectors.ce.config import CeSettings, get_ce_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.transforms.compose import compose, referenced_ids
from gasbalance_etl.validation.canonical import canonical_schema

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "ce"
schema = canonical_schema

_HISTORY_START = dt.date(2014, 1, 1)
# ponytail: ~60 ids/request keeps the URL well under limits; 6-way concurrency clears the
# ~5 batches in one round. Raise both if CE tolerates it and fetching is the bottleneck.
_BATCH_IDS = 60
_MAX_CONCURRENCY = 6


def series_dict() -> list[dict[str, Any]]:
    return load_series_dict(source)


def _parse_multi(csv_text: str) -> dict[str, pd.Series]:
    """Parse a multi-id `eugasseries` CSV into {ce_id: Series(index=date)}.

    Columns: `DateExcel`, `Date` (e.g. `01-Apr-2022`), then one column per requested id.
    Dedupes by calendar date (mean) and drops missing values.
    """
    df = pd.read_csv(io.StringIO(csv_text))
    if "Date" not in df.columns:
        return {}
    dates = pd.to_datetime(df["Date"], format="%d-%b-%Y").dt.date
    out: dict[str, pd.Series] = {}
    for col in df.columns:
        if col in ("Date", "DateExcel"):
            continue
        s = pd.Series(df[col].to_numpy(), index=dates).dropna()
        out[str(col)] = s.groupby(level=0).mean()
    return out


async def _fetch_all(cfg: CeSettings, ids: list[str], start: str, end: str) -> pd.DataFrame:
    """Fetch all raw ids in batched, concurrent requests -> wide df (index=date, cols=id)."""
    batches = [ids[i : i + _BATCH_IDS] for i in range(0, len(ids), _BATCH_IDS)]
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        auth=httpx.BasicAuth(cfg.username, cfg.password),
        headers={"Accept": "text/csv"},
        timeout=180.0,
    ) as client:

        async def _one(batch: list[str]) -> str:
            async with sem:
                resp = await client.get(
                    "eugasseries",
                    params={"id": ",".join(batch), "dateFrom": start, "dateTo": end, "unit": "mcm"},
                )
                resp.raise_for_status()
                log.info("ce: fetched batch of %d ids", len(batch))
                return resp.text

        texts = await asyncio.gather(*[_one(b) for b in batches])

    series_map: dict[str, pd.Series] = {}
    for txt in texts:
        series_map.update(_parse_multi(txt))
    wide = pd.DataFrame(series_map)
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Full refresh: fetch every raw CE series the dictionary needs, since 2014.

    `since` is accepted (framework contract) but ignored — full refresh by design.
    """
    del since  # ponytail: full refresh; idempotent upsert makes re-runs safe
    cfg = get_ce_settings()
    ids = referenced_ids(series_dict())
    n_batches = -(-len(ids) // _BATCH_IDS)
    log.info("ce: fetching %d raw series in %d batches", len(ids), n_batches)
    start, end = _HISTORY_START.isoformat(), dt.date.today().isoformat()
    return asyncio.run(_fetch_all(cfg, ids, start, end))


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Compose the canonical series from the wide raw frame, per `settings/ce.yaml`."""
    return compose(series_dict(), raw, source)
