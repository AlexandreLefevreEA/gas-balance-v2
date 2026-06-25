"""Idempotent upsert of canonical data into Postgres (series + observation).

Re-fetching the whole history and calling these is safe: `(series_id, obs_date)` is
the observation PK, so a second run overwrites values in place — no duplicates, no
deletes. Series dropped from the dictionary keep their rows and history.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from gasbalance_core.models import Observation, Series

# ponytail: 10k rows x 4 cols = 40k bind params, safely under Postgres' 65535 limit;
# fewer round-trips than 5k for the ~1M-row CE full-history upsert. COPY-into-staging
# would beat this if the upsert ever dominates a tight schedule.
_BATCH = 10000


def sync_series(
    session: Session, dictionary: Sequence[Mapping[str, Any]], source: str
) -> dict[str, int]:
    """Upsert `series` rows from the curated dictionary; return `{code: id}`."""
    if not dictionary:
        return {}
    rows = [
        {
            "code": d["code"],
            "name": d["name"],
            "category": d.get("group"),
            "sub_group": d.get("sub_group"),
            "area": d.get("area"),
            "unit": d.get("unit", "mcm"),
            "source": source,
        }
        for d in dictionary
    ]
    ins = pg_insert(Series).values(rows)
    stmt = ins.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": ins.excluded.name,
            "category": ins.excluded.category,
            "sub_group": ins.excluded.sub_group,
            "area": ins.excluded.area,
            "unit": ins.excluded.unit,
            "source": ins.excluded.source,
        },
    ).returning(Series.id, Series.code)
    return {code: id_ for id_, code in session.execute(stmt).all()}


def upsert_observations(
    session: Session, df: pd.DataFrame, run_id: int, code_to_id: Mapping[str, int]
) -> int:
    """Upsert observations from a canonical frame. Returns rows written."""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        series_id = code_to_id.get(rec["series_id"])
        if series_id is None:
            continue  # series not in the dictionary; the dictionary drives the fetch
        raw_date = rec["date"]
        obs_date = raw_date.date() if isinstance(raw_date, dt.datetime | pd.Timestamp) else raw_date
        rows.append(
            {
                "series_id": series_id,
                "obs_date": obs_date,
                "value": float(rec["value"]),
                "run_id": run_id,
            }
        )

    written = 0
    for i in range(0, len(rows), _BATCH):
        chunk = rows[i : i + _BATCH]
        stmt = pg_insert(Observation).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["series_id", "obs_date"],
            set_={
                "value": stmt.excluded.value,
                "run_id": stmt.excluded.run_id,
                "loaded_at": func.now(),
            },
        )
        session.execute(stmt)
        written += len(chunk)
    return written
