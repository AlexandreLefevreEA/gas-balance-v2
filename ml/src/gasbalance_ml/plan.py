"""The forecast plan — which model family forecasts each balance component, and what
covariates that family needs.

Legacy dispatched every series **by name** (`models/scenarios/forecast_scenario.py`); we
replicate that here, but key off v2's structural `(category, sub_group)` where it's
equivalent (cleaner than string-suffix matching) and fall back to the legacy **name** only
for the handful of named points that don't fit a group rule (Gela, Pirineos, Moffat, …).

This module is pure (no DB) so the classification and the covariate-presence logic are
unit-testable; `data.read_forecast_plan` reads the dictionary rows and calls `family_of`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanRow:
    code: str
    name: str
    area: str | None
    family: str


# Named points that override the group-based rules (legacy forecast_scenario.py name matches).
_NAMED: dict[str, str] = {
    "Pirineos": "pirineos",
    "Moffat": "moffat",
    "Kyustendil": "bounded_persistence",
    "Azeri (Melendugno)": "azeri",
    "Azeri (Kipoi,Kipi)": "azeri",
    "Turk Stream (Malkoclar)": "average_plus_outage",
    "Gela": "average_plus_outage",
    "Mazara": "average_plus_outage",
    # Switzerland / Luxembourg / Serbia border flows are demand-proxies with no `area`; legacy
    # forecast them like demand on German temps (`area="Germany"` default) — same as Moffat.
    "CH Border Flows": "moffat",
    "LU Border Flows": "moffat",
    "RS Border Flows": "moffat",
}


def family_of(name: str, category: str | None, sub_group: str | None) -> str | None:
    """The model family for a component, or None if legacy didn't forecast it (most pipelines,
    generic border flows, storage withdrawal/level — the last two are the balance residual).

    Order matters: a named override wins over the suffix/group rule (e.g. Turk Stream is a
    `border_flows` series but legacy ran the production model on it).

    Demand is dispatched by **name suffix** (legacy `forecast_scenario.py` parity), NOT
    `sub_group`: in the loaded dictionary the CE demand series carry `sub_group=NULL` and their
    type lives only in the name ("DE LDZ", "DE GTP", "CZ Demand"). The suffix check also excludes
    the `KP.LOAD.*` electricity-load covariates that share `category='demand'` — their names end
    in lowercase " power demand", not "LDZ"/"IND"/"GTP"/"Demand". (`sub_group` *is* reliable for
    lng/storage — level/capacity/withdrawal — so those branches still use it.)"""
    if name in _NAMED:
        return _NAMED[name]
    if name.endswith("GTP"):
        return "gtp"  # power-driven
    if name.endswith(("LDZ", "IND")) or name.endswith("Demand"):
        return "demand"  # temperature path (LightGBM / seasonal_naive); "Demand" = country total
    if category == "production":
        return "average_plus_outage"
    if category == "lng":
        if sub_group is None:  # LNG sendout (level/capacity are excluded)
            return "seasonal_mean"
        if sub_group == "capacity":
            return "ffill"
        return None
    if category == "storage":
        return "ffill" if sub_group == "capacity" else None  # withdrawal/level = closure residual
    if category == "supply":  # linepack + CE imbalance
        return "absolute_zero"
    return None  # pipeline / border_flows (generic), anything else


# --- covariate requirements per family (for the presence check) ---------------------------

# Power-spot zone has its own naming (DE→DE-LU etc.); load/gen/avail use the plain country code.
_SPOT_ZONE: dict[str, str] = {"DE": "DE-LU", "DK": "DK1", "IT": "IT-NORTH"}
# GB has no power spot in v2; legacy substitutes German power covariates for UK GTP.
_GTP_COV_AREA: dict[str, str] = {"GB": "DE"}


def required_covariates(family: str, area: str | None) -> list[str]:
    """The covariate series codes a family needs at `area`. Empty for the arithmetic supply
    models (they project their own target). Used only to warn when an input is missing."""
    if family in ("demand",):
        return [f"KP.TEMP.{area}"] if area else []
    if family == "moffat":
        return ["KP.TEMP.DE"]  # legacy quirk: Moffat forecasts on German temps
    if family == "pirineos":
        return ["KP.GASSPOT.PVB", "KP.GASSPOT.PEG"]  # Spain-vs-France gas spread
    if family == "gtp":
        a = _GTP_COV_AREA.get(area or "", area or "")
        return [
            f"KP.SPOT.{_SPOT_ZONE.get(a, a)}",  # power price
            "KP.GASSPOT.TTF",  # gas benchmark (legacy spark spread uses TTF)
            "KP.CARBON.SPOT",  # EUA carbon
            f"KP.LOAD.{a}",  # electricity load (residual = load minus renewables)
            f"KP.GEN.GAS.{a}",  # gas-fired generation
            f"KP.AVAIL.GAS.{a}",  # gas plant availability
        ]
    return []  # average_plus_outage / seasonal_mean / azeri / ffill / absolute_zero / bounded


def check_covariates(
    rows: Sequence[PlanRow], present: Callable[[Iterable[str]], set[str]]
) -> dict[str, list[str]]:
    """Warn for every plan row whose required covariates are absent, **Germany first** (the
    user's ask). `present(codes)` returns the subset that exist with data in the dictionary.
    Returns `{series_code: [missing covariate codes]}` (also for assertions in tests)."""
    needed: dict[str, list[str]] = {r.code: required_covariates(r.family, r.area) for r in rows}
    have = present({c for codes in needed.values() for c in codes})
    missing = {r.code: [c for c in needed[r.code] if c not in have] for r in rows}
    missing = {code: m for code, m in missing.items() if m}
    if not missing:
        return {}

    by_code = {r.code: r for r in rows}
    de = sorted(c for c in missing if by_code[c].area == "DE")
    rest = sorted(c for c in missing if by_code[c].area != "DE")
    for code in de:  # Germany called out explicitly, first
        log.warning(
            "covariate-check: GERMANY %s (%s) missing %s", code, by_code[code].family, missing[code]
        )
    for code in rest:
        log.warning(
            "covariate-check: %s (%s) missing %s", code, by_code[code].family, missing[code]
        )
    return missing
