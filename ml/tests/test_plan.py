"""family_of dispatch, required_covariates, and the Germany-first covariate-presence warning."""

from __future__ import annotations

import logging
from collections.abc import Iterable

import pytest

from gasbalance_ml.plan import PlanRow, check_covariates, family_of, required_covariates


def test_family_of_dispatch() -> None:
    cases = {
        # (name, category, sub_group) -> family
        ("DE LDZ", "demand", "LDZ"): "demand",
        ("DE IND", "demand", "IND"): "demand",
        ("CZ Demand", "demand", "total"): "demand",
        ("DE GTP", "demand", "GTP"): "gtp",
        ("DE Prod", "production", None): "average_plus_outage",
        ("DK Biogas Prod", "production", None): "average_plus_outage",
        ("NL LNG", "lng", None): "seasonal_mean",
        ("DE LNG Level", "lng", "level"): None,
        ("DE LNG capacity", "lng", "capacity"): "ffill",
        ("DE Storage Capacity", "storage", "capacity"): "ffill",
        ("DE Storage Withdrawal", "storage", "withdrawal"): None,  # closure residual
        ("DE Storage Level", "storage", "level"): None,
        ("DE Linepack", "supply", None): "absolute_zero",
        ("DE CE Imbalance", "supply", None): "absolute_zero",
        ("DE Pipeline", "pipeline", None): None,  # generic pipeline: not forecast
        ("Nord Stream 1+2", "pipeline", None): None,
        # named overrides win over the group rule
        ("Gela", "pipeline", None): "average_plus_outage",
        ("Turk Stream (Malkoclar)", "border_flows", None): "average_plus_outage",
        ("Azeri (Melendugno)", "pipeline", None): "azeri",
        ("Pirineos", "border_flows", None): "pirineos",
        ("Moffat", "border_flows", None): "moffat",
        ("Kyustendil", "border_flows", None): "bounded_persistence",
        ("CH Border Flows", "border_flows", None): "demand",
        ("Waidhaus", "border_flows", None): None,  # generic border flow: not forecast
    }
    for (name, cat, sg), want in cases.items():
        assert family_of(name, cat, sg) == want, (name, cat, sg)


def test_required_covariates() -> None:
    assert required_covariates("demand", "DE") == ["KP.TEMP.DE"]
    assert required_covariates("moffat", "GB") == ["KP.TEMP.DE"]  # legacy German-temp quirk
    assert required_covariates("pirineos", None) == ["KP.GASSPOT.PVB", "KP.GASSPOT.PEG"]
    assert required_covariates("average_plus_outage", "NL") == []  # arithmetic, no covariate

    de_gtp = required_covariates("gtp", "DE")
    assert "KP.SPOT.DE-LU" in de_gtp and "KP.LOAD.DE" in de_gtp and "KP.CARBON.SPOT" in de_gtp
    gb_gtp = required_covariates("gtp", "GB")
    assert "KP.LOAD.DE" in gb_gtp and "KP.SPOT.DE-LU" in gb_gtp  # GB GTP runs on German covariates


def test_check_covariates_returns_missing() -> None:
    rows = [
        PlanRow("CE.54", "DE LDZ", "DE", "demand"),
        PlanRow("CE.62", "FR LDZ", "FR", "demand"),
        PlanRow("CE.32", "DE Prod", "DE", "average_plus_outage"),  # needs nothing
    ]

    def present(codes: Iterable[str]) -> set[str]:  # FR temp present, DE temp absent
        return {c for c in codes if c != "KP.TEMP.DE"}

    missing = check_covariates(rows, present)
    assert missing == {"CE.54": ["KP.TEMP.DE"]}  # only DE LDZ; production needs no covariate


def test_check_covariates_warns_germany_first(caplog: pytest.LogCaptureFixture) -> None:
    rows = [
        PlanRow("CE.62", "FR LDZ", "FR", "demand"),
        PlanRow("CE.54", "DE LDZ", "DE", "demand"),
    ]

    def present(codes: Iterable[str]) -> set[str]:
        return set()  # nothing present -> both missing

    with caplog.at_level(logging.WARNING, logger="gasbalance_ml.plan"):
        check_covariates(rows, present)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("GERMANY CE.54" in m for m in msgs)
    # Germany is logged before the rest.
    de_idx = next(i for i, m in enumerate(msgs) if "CE.54" in m)
    fr_idx = next(i for i, m in enumerate(msgs) if "CE.62" in m)
    assert de_idx < fr_idx


if __name__ == "__main__":
    test_family_of_dispatch()
    test_required_covariates()
    test_check_covariates_returns_missing()
    print("ok")
