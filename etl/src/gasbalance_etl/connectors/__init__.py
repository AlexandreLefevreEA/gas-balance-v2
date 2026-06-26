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
from gasbalance_etl.connectors.ecb_fx import connector as _ecb_fx
from gasbalance_etl.connectors.eq_coal_curve import connector as _eq_coal
from gasbalance_etl.connectors.kpler_actual_temps import connector as _kpler
from gasbalance_etl.connectors.kpler_availability import connector as _kpler_avail
from gasbalance_etl.connectors.kpler_availability_forecast import connector as _kpler_avail_fc
from gasbalance_etl.connectors.kpler_carbon_settles import connector as _kpler_carbon_settles
from gasbalance_etl.connectors.kpler_carbon_spot import connector as _kpler_carbon
from gasbalance_etl.connectors.kpler_gas_forward_curve import connector as _kpler_gas_fc
from gasbalance_etl.connectors.kpler_gas_spot import connector as _kpler_gas_spot
from gasbalance_etl.connectors.kpler_generation_actual import connector as _kpler_gen
from gasbalance_etl.connectors.kpler_generation_forecast import connector as _kpler_gen_fc
from gasbalance_etl.connectors.kpler_generation_long_term import connector as _kpler_gen_lt
from gasbalance_etl.connectors.kpler_long_term_temperatures import connector as _kpler_lt
from gasbalance_etl.connectors.kpler_power_demand import connector as _kpler_demand
from gasbalance_etl.connectors.kpler_power_demand_forecast import connector as _kpler_demand_fc
from gasbalance_etl.connectors.kpler_power_demand_long_term import connector as _kpler_demand_lt
from gasbalance_etl.connectors.kpler_power_forward_curve import connector as _kpler_pfc
from gasbalance_etl.connectors.kpler_power_spot import connector as _kpler_spot
from gasbalance_etl.connectors.kpler_temps_forecast import connector as _kpler_fc
from gasbalance_etl.transforms import carbon_curve as _carbon_curve
from gasbalance_etl.transforms import derived as _derived

REGISTRY: dict[str, Any] = {
    _ce.source: _ce,
    _ecb_fx.source: _ecb_fx,
    _eq_coal.source: _eq_coal,
    _kpler.source: _kpler,
    _kpler_avail.source: _kpler_avail,
    _kpler_avail_fc.source: _kpler_avail_fc,
    _kpler_carbon.source: _kpler_carbon,
    _kpler_carbon_settles.source: _kpler_carbon_settles,
    _kpler_gas_fc.source: _kpler_gas_fc,
    _kpler_gas_spot.source: _kpler_gas_spot,
    _kpler_gen.source: _kpler_gen,
    _kpler_gen_fc.source: _kpler_gen_fc,
    _kpler_gen_lt.source: _kpler_gen_lt,
    _kpler_lt.source: _kpler_lt,
    _kpler_fc.source: _kpler_fc,
    _kpler_demand.source: _kpler_demand,
    _kpler_demand_fc.source: _kpler_demand_fc,
    _kpler_demand_lt.source: _kpler_demand_lt,
    _kpler_pfc.source: _kpler_pfc,
    _kpler_spot.source: _kpler_spot,
    # transforms read what the raw sources loaded -> registered after them (ADR 0007)
    _carbon_curve.source: _carbon_curve,  # reads KP.CARBON.SPOT + KP.CARBON.SETTLES
    _derived.source: _derived,  # keep last
}
