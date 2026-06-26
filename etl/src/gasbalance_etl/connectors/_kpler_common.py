"""Shared helpers for the Kpler connectors — incremental load-state queries, date-window
chunking, and the forecast-covariate retention rule.

These were byte-identical (or trivially varying) across the connectors; they live here once.
The two DB queries import `gasbalance_core` **inside** the function (lazily) so importing this
module — and therefore importing a connector for the registry — never builds the engine,
keeping the fixture-based connector tests DB-free (the same convention the connectors use).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Forecast-covariate retention policy, shared by every `*_forecast` connector: keep all daily
# runs this many days back, plus every Monday run for a year (see the connector docstrings).
KEEP_DAILY_DAYS = 15
KEEP_MONDAY_DAYS = 365


def last_loaded_ts(source: str) -> dt.datetime | None:
    """Latest `covariate.ts` already stored for `source` (or None) — drives incremental fetch."""
    from sqlalchemy import func, select

    from gasbalance_core.db import SessionLocal
    from gasbalance_core.models import Covariate, Series

    stmt = (
        select(func.max(Covariate.ts))
        .join(Series, Covariate.series_id == Series.id)
        .where(Series.source == source)
    )
    with SessionLocal() as session:
        return session.execute(stmt).scalar_one_or_none()


def loaded_run_dates(source: str) -> set[dt.date]:
    """Distinct `forecast_covariate.made_on` already stored for `source` (drives gap backfill)."""
    from sqlalchemy import select

    from gasbalance_core.db import SessionLocal
    from gasbalance_core.models import ForecastCovariate, Series

    stmt = (
        select(ForecastCovariate.made_on)
        .join(Series, ForecastCovariate.series_id == Series.id)
        .where(Series.source == source)
        .distinct()
    )
    with SessionLocal() as session:
        return set(session.execute(stmt).scalars().all())


def date_chunks(start: dt.date, end: dt.date, chunk_days: int) -> list[tuple[dt.date, dt.date]]:
    """Split [start, end) into <= `chunk_days` windows (the API's endDate is exclusive)."""
    chunks: list[tuple[dt.date, dt.date]] = []
    cur = start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=chunk_days), end)
        chunks.append((cur, nxt))
        cur = nxt
    return chunks


def desired_run_dates(
    today: dt.date,
    *,
    floor: dt.date | None = None,
    keep_daily: int = KEEP_DAILY_DAYS,
    keep_monday: int = KEEP_MONDAY_DAYS,
) -> list[dt.date]:
    """The retention keep-set: every day in the last `keep_daily` days + every Monday in the last
    `keep_monday` days. Fetching exactly this set means we never pull a vintage we'd immediately
    prune. `floor` clamps out run dates that predate the data (else they'd be re-requested every
    run, returning empty forever).
    """
    daily = {today - dt.timedelta(days=i) for i in range(keep_daily + 1)}
    mondays = {
        d for i in range(keep_monday + 1) if (d := today - dt.timedelta(days=i)).weekday() == 0
    }
    keep = daily | mondays
    if floor is not None:
        keep = {d for d in keep if d >= floor}
    return sorted(keep)


def vintages_to_delete(
    made_ons: list[dt.date],
    today: dt.date,
    *,
    keep_daily: int = KEEP_DAILY_DAYS,
    keep_monday: int = KEEP_MONDAY_DAYS,
) -> set[dt.date]:
    """Run dates to drop: keep all of the last `keep_daily` days + every Monday for `keep_monday`
    days, delete the rest. Pure (no I/O) so the retention rule is unit-tested directly; the
    connectors' `prune` wraps it in SQL.
    """
    recent = today - dt.timedelta(days=keep_daily)
    year = today - dt.timedelta(days=keep_monday)
    out: set[dt.date] = set()
    for d in made_ons:
        if d >= recent:
            continue  # within the daily window: keep all
        if d.weekday() == 0:  # Monday
            if d < year:
                out.add(d)  # Monday older than a year: delete
        else:
            out.add(d)  # non-Monday outside the window: delete
    return out


def prune_vintages(session: Session, source: str) -> int:
    """Delete forecast vintages outside the retention window for `source`. Returns rows deleted.

    Runs in the caller's transaction (the connector's `load` hook commits on success, so a failed
    load rolls back the prune too). The `*_forecast` connectors expose this via their `prune` hook
    (`etl prune <source>`). See ADR 0009.
    """
    from sqlalchemy import delete, select

    from gasbalance_core.models import ForecastCovariate, Series

    today = dt.datetime.now(dt.UTC).date()
    sids = select(Series.id).where(Series.source == source)
    made_ons = list(
        session.execute(
            select(ForecastCovariate.made_on)
            .where(ForecastCovariate.series_id.in_(sids))
            .distinct()
        ).scalars()
    )
    to_delete = vintages_to_delete(made_ons, today)
    if not to_delete:
        return 0
    result = session.execute(
        delete(ForecastCovariate).where(
            ForecastCovariate.series_id.in_(sids),
            ForecastCovariate.made_on.in_(to_delete),
        )
    )
    deleted = int(cast(Any, result).rowcount or 0)  # DML CursorResult.rowcount
    log.info("%s: pruned %d rows across %d vintages", source, deleted, len(to_delete))
    return deleted
