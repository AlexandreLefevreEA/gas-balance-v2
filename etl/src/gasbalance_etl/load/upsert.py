"""Idempotent upsert of canonical data into Postgres (series + observation/covariate).

Re-fetching the whole history and calling these is safe: each table's natural key is its
PK — e.g. `(series_id, obs_date)` for observations — so a second run overwrites values in
place (no duplicates, no deletes). Series dropped from the dictionary keep their rows.

Bulk path: rows are streamed via `COPY` into an `ON COMMIT DROP` temp table, then merged with a
single `INSERT … SELECT … ON CONFLICT DO UPDATE`. That's far fewer round-trips (and no per-row
bind-param compilation) than chunked `INSERT … VALUES` — the win on the big full-history loads.
The COPY runs on the session's own connection/transaction, so it commits/rolls back with the run.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from gasbalance_core.models import Covariate, ForecastCovariate, Observation, Series


def _copy_upsert(
    session: Session,
    table: Any,
    columns: list[str],
    coltypes: list[str],
    conflict: list[str],
    rows: list[tuple[Any, ...]],
) -> int:
    """COPY `rows` into a temp table, then merge into `table` (overwrite on PK). Returns # rows.

    `columns` lists the copied columns in row order with the PK (`conflict`) columns **first**, so
    `row[:len(conflict)]` is the key — used to drop in-batch duplicates (last wins) before COPY,
    since one `INSERT … ON CONFLICT` can't touch the same target row twice. `coltypes` are the temp
    column SQL types. `value`/`run_id`/`loaded_at` are refreshed on conflict; `loaded_at` defaults
    to now() on insert.
    """
    if not rows:
        return 0
    key_n = len(conflict)
    deduped = {row[:key_n]: row for row in rows}  # last occurrence wins (idempotent overwrite)
    rows = list(deduped.values())

    tbl = table.__table__
    # schema comes from config (Base.metadata), never hardcoded
    target = f"{tbl.schema}.{tbl.name}" if tbl.schema else tbl.name
    tmp = f"_tmp_{tbl.name}"
    cols_sql = ", ".join(columns)
    coldefs = ", ".join(f"{c} {t}" for c, t in zip(columns, coltypes, strict=True))
    conflict_sql = ", ".join(conflict)
    # Raw psycopg3 connection bound to the session's transaction → COPY commits with the run.
    dbapi = session.connection().connection.driver_connection
    assert dbapi is not None  # an open session always has a live driver connection

    with dbapi.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
        cur.execute(f"CREATE TEMP TABLE {tmp} ({coldefs}) ON COMMIT DROP")
        with cur.copy(f"COPY {tmp} ({cols_sql}) FROM STDIN") as cp:
            for row in rows:
                cp.write_row(row)
        cur.execute(
            f"INSERT INTO {target} ({cols_sql}) SELECT {cols_sql} FROM {tmp} "
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET "
            f"value = excluded.value, run_id = excluded.run_id, loaded_at = now()"
        )
    return len(rows)


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
            # canonical uses `group`; the ORM column is `category` (`group` is a SQL reserved word)
            "category": d.get("group"),
            "sub_group": d.get("sub_group"),
            "area": d.get("area"),
            "unit": d.get("unit", "mcm"),
            "source": source,
            "is_derived": d.get("is_derived", False),
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
            "is_derived": ins.excluded.is_derived,
        },
    ).returning(Series.id, Series.code)
    return {code: id_ for id_, code in session.execute(stmt).all()}


def upsert_observations(
    session: Session, df: pd.DataFrame, run_id: int, code_to_id: Mapping[str, int]
) -> int:
    """Upsert observations from a canonical frame. Returns rows written."""
    rows: list[tuple[Any, ...]] = []
    for rec in df.to_dict("records"):
        series_id = code_to_id.get(rec["series_id"])
        if series_id is None:
            continue  # series not in the dictionary; the dictionary drives the fetch
        raw_date = rec["date"]
        obs_date = raw_date.date() if isinstance(raw_date, dt.datetime | pd.Timestamp) else raw_date
        rows.append((series_id, obs_date, float(rec["value"]), run_id))
    return _copy_upsert(
        session,
        Observation,
        ["series_id", "obs_date", "value", "run_id"],
        ["integer", "date", "double precision", "integer"],
        ["series_id", "obs_date"],
        rows,
    )


def upsert_forecast_covariates(
    session: Session, df: pd.DataFrame, run_id: int, code_to_id: Mapping[str, int]
) -> int:
    """Upsert hourly forecast covariates into `forecast_covariate`. Returns rows written.

    Like `upsert_covariates`, but the canonical frame also carries a `made_on` (the
    forecast run date) which joins the PK `(series_id, made_on, ts)` — so every vintage is
    kept, not overwritten. Re-fetching a present vintage is a no-op (idempotent upsert).
    The connector enforces a retention policy separately. See ADR 0009.
    """
    rows: list[tuple[Any, ...]] = []
    for rec in df.to_dict("records"):
        series_id = code_to_id.get(rec["series_id"])
        if series_id is None:
            continue  # series not in the dictionary; the dictionary drives the fetch
        ts = pd.Timestamp(rec["date"])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        made_on = rec["made_on"]
        made_on = made_on.date() if isinstance(made_on, dt.datetime | pd.Timestamp) else made_on
        rows.append((series_id, made_on, ts.to_pydatetime(), float(rec["value"]), run_id))
    return _copy_upsert(
        session,
        ForecastCovariate,
        ["series_id", "made_on", "ts", "value", "run_id"],
        ["integer", "date", "timestamptz", "double precision", "integer"],
        ["series_id", "made_on", "ts"],
        rows,
    )


def upsert_covariates(
    session: Session, df: pd.DataFrame, run_id: int, code_to_id: Mapping[str, int]
) -> int:
    """Upsert sub-daily covariates (e.g. hourly temperature) into `covariate`.

    Same canonical frame as observations, but the `date` column carries a full
    timestamp and is stored as-is in `covariate.ts` (no truncation to day). Naive
    timestamps are read as UTC. Connectors point their `load` hook here. See ADR 0008.
    """
    rows: list[tuple[Any, ...]] = []
    for rec in df.to_dict("records"):
        series_id = code_to_id.get(rec["series_id"])
        if series_id is None:
            continue  # series not in the dictionary; the dictionary drives the fetch
        ts = pd.Timestamp(rec["date"])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        rows.append((series_id, ts.to_pydatetime(), float(rec["value"]), run_id))
    return _copy_upsert(
        session,
        Covariate,
        ["series_id", "ts", "value", "run_id"],
        ["integer", "timestamptz", "double precision", "integer"],
        ["series_id", "ts"],
        rows,
    )
