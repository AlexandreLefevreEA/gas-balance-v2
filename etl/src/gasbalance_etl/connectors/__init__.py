"""Connector registry — the CLI dispatches `etl run <source>` on this.

Each connector exposes the same module-level interface, so the CLI pipeline is fully
source-agnostic: `source`, `schema`, and the `fetch` / `to_canonical` / `series_dict`
callables. CE is the first instance; add a source by importing it and adding it here.
"""

from __future__ import annotations

from typing import Any

from gasbalance_etl.connectors.ce import connector as _ce

REGISTRY: dict[str, Any] = {_ce.source: _ce}
