"""Connector registry — the CLI dispatches `etl run <source>` on this.

Each connector exposes the same module-level interface, so the CLI pipeline is fully
source-agnostic: `source`, `schema`, and the `fetch` / `to_canonical` / `series_dict`
callables. CE is the first instance; add a source by importing it and adding it here.

The derived stage (`transforms/derived`) satisfies the same interface but reads its
inputs from Postgres; it is registered LAST so `etl run all` computes derived series
after the raw sources have loaded (see ADR 0007).
"""

from __future__ import annotations

from typing import Any

from gasbalance_etl.connectors.ce import connector as _ce
from gasbalance_etl.transforms import derived as _derived

REGISTRY: dict[str, Any] = {_ce.source: _ce, _derived.source: _derived}
