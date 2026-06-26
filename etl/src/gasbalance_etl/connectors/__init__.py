"""Connector registry — the CLI dispatches `etl run <source>` on this.

Each connector exposes the same module-level interface, so the CLI pipeline is fully
source-agnostic: `source`, `schema`, the `fetch` / `to_canonical` / `series_dict`
callables, and an optional `load` hook that overrides the default observation sink (e.g.
`kpler_actual_temps` routes to the hourly `covariate` table — ADR 0008). Add a source by
importing it and adding it here.

The derived stage (`transforms/derived`) satisfies the same interface but reads its
inputs from Postgres; it is registered LAST so `etl run all` computes derived series
after the raw sources have loaded (see ADR 0007).
"""

from __future__ import annotations

from typing import Any

from gasbalance_etl.connectors.ce import connector as _ce
from gasbalance_etl.connectors.kpler_actual_temps import connector as _kpler
from gasbalance_etl.connectors.kpler_generation_actual import connector as _kpler_gen
from gasbalance_etl.connectors.kpler_long_term_temperatures import connector as _kpler_lt
from gasbalance_etl.connectors.kpler_temps_forecast import connector as _kpler_fc
from gasbalance_etl.transforms import derived as _derived

REGISTRY: dict[str, Any] = {
    _ce.source: _ce,
    _kpler.source: _kpler,
    _kpler_gen.source: _kpler_gen,
    _kpler_lt.source: _kpler_lt,
    _kpler_fc.source: _kpler_fc,
    _derived.source: _derived,  # keep last: reads what the raw sources loaded
}
