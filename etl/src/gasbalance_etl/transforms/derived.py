"""Derived series stage — computes is_derived series from already-loaded data.

A connector under the standard ETL contract (`source / schema / fetch /
to_canonical / series_dict`), but `fetch` reads its inputs from Postgres — the v2
series referenced by `settings/derived.yaml`, selected by group/sub_group — instead
of the network. value = sum(positive) - sum(negative) via the shared compose
primitive; results land in `observation` flagged is_derived.

Registered last so `etl run all` runs it after the raw sources. See ADR 0007.

`gasbalance_core.db`/`.models` are imported inside the DB functions, not at module
top: importing this module (for the connector registry) must not create the engine,
so the fixture-based tests stay DB-free.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import select

from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.transforms.compose import compose, referenced_ids, resolve
from gasbalance_etl.validation.canonical import canonical_schema
from gasbalance_etl.validation.identities import check_identities

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "derived"
schema = canonical_schema
is_transform = True  # reads raw outputs from Postgres -> the CLI runs it after the raw phase


def series_dict() -> list[dict[str, Any]]:
    return load_series_dict(source)


def _catalog(session: Session) -> dict[str, dict[str, Any]]:
    """Map raw (is_derived=false) series code -> {group, sub_group, area} for selection."""
    from gasbalance_core.models import Series

    stmt = select(Series.code, Series.category, Series.sub_group, Series.area).where(
        Series.is_derived.is_(False)
    )
    return {
        # ORM `category` maps back to canonical `group` (renamed: `group` is a SQL reserved word)
        code: {"group": cat, "sub_group": sg, "area": ar}
        for code, cat, sg, ar in session.execute(stmt).all()
    }


def _read_inputs(session: Session, codes: list[str]) -> pd.DataFrame:
    """Read observations for the given series codes into a wide frame (date x code)."""
    from gasbalance_core.models import Observation, Series

    if not codes:
        return pd.DataFrame()
    stmt = (
        select(Series.code, Observation.obs_date, Observation.value)
        .join(Observation, Observation.series_id == Series.id)
        .where(Series.code.in_(codes))
    )
    rows = [tuple(r) for r in session.execute(stmt).all()]
    if not rows:
        return pd.DataFrame()
    long = pd.DataFrame(rows, columns=["code", "date", "value"])
    wide = long.pivot(index="date", columns="code", values="value")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Read every v2 series the derived dictionary references, from Postgres.

    `since` is accepted (framework contract) but ignored — full recompute; the
    idempotent upsert makes re-runs safe.
    """
    del since  # full recompute
    from gasbalance_core.db import SessionLocal

    with SessionLocal() as session:
        resolved = resolve(series_dict(), _catalog(session))
        codes = referenced_ids(resolved)
        log.info("derived: reading %d input series from postgres", len(codes))
        return _read_inputs(session, codes)


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Compose derived series from the wide input frame, then check balance identities."""
    from gasbalance_core.db import SessionLocal

    with SessionLocal() as session:
        resolved = resolve(series_dict(), _catalog(session))
    df = compose(resolved, raw, source)
    check_identities(df, resolved)
    return df
