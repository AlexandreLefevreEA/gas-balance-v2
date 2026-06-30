"""Write forecasts to Postgres — the one write-bound ml module (the rest is pure / read-only).

Mirrors the etl idempotent-upsert pattern (`pg_insert … on_conflict_do_update`) but lives in
`ml` because `ml` must not depend on `etl`. `publish_forecasts` is the single entry the
forecast pipeline calls: it ensures the scenarios exist (FK), opens a `forecast_run` audit
row, upserts every `(series, scenario, target_date, made_on)` cell, and closes the run.
Re-running the same `made_on` overwrites that vintage in place (the 5-column PK).

`core.db`/`core.models` are imported inside functions so importing this module never builds
the engine (same convention as `data.py`).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Postgres caps one statement at 65535 bound parameters; `pg_insert().values()` binds every
# column of every row, so rows-per-INSERT must stay under 65535 / n_columns. (etl sidesteps
# this with COPY; if forecast volume grows, port that path here — see module docstring.)
_PG_MAX_PARAMS = 65535


def _describe(code: str) -> str:
    if code == "MEAN":
        return "Climatological-normal weather"
    if code.startswith("REF_"):
        return f"Weather replay of {code[4:]}"
    if "@" in code:  # materialized custom combo, <custom>@<weather>
        custom, weather = code.split("@", 1)
        return f"Custom '{custom}' on {_describe(weather)}"
    return code


def ensure_scenarios(session: Session, codes: Iterable[str]) -> None:
    """Upsert the `scenario` rows the forecasts reference (FK), without clobbering existing
    descriptions/flags (`do_nothing` on conflict). A `<custom>@<weather>` combo is tagged
    `kind='custom'`; everything else is a weather row. Authored custom *definition* rows
    (with `adjustments`) are written elsewhere and never appear here as forecast keys."""
    from gasbalance_core.models import Scenario

    rows = [
        {"code": c, "description": _describe(c), "kind": "custom" if "@" in c else "weather"}
        for c in dict.fromkeys(codes)
    ]
    if not rows:
        return
    session.execute(
        pg_insert(Scenario).values(rows).on_conflict_do_nothing(index_elements=["code"])
    )


def series_ids(session: Session, codes: Iterable[str]) -> dict[str, int]:
    """`{code: id}` for the given series codes (one query)."""
    from gasbalance_core.models import Series

    wanted = list(dict.fromkeys(codes))
    if not wanted:
        return {}
    rows = session.execute(select(Series.code, Series.id).where(Series.code.in_(wanted))).all()
    return {str(code): int(sid) for code, sid in rows}


def open_forecast_run(session: Session) -> int:
    """Insert a `forecast_run` audit row (status defaults to 'running'); return its id."""
    from gasbalance_core.models import ForecastRun

    run = ForecastRun()
    session.add(run)
    session.flush()  # populate the Identity run_id
    return int(run.run_id)


def close_forecast_run(
    session: Session, run_id: int, status: str, message: str | None = None
) -> None:
    from gasbalance_core.models import ForecastRun

    run = session.get(ForecastRun, run_id)
    if run is not None:
        run.status = status
        run.finished_at = dt.datetime.now(dt.UTC)
        run.message = message


def upsert_forecasts(
    session: Session, rows: Sequence[Mapping[str, Any]], run_id: int, code_to_id: Mapping[str, int]
) -> int:
    """Upsert forecast cells. Each row: series_code, target_date, scenario, model_run_id,
    made_on, value. Idempotent on the 5-column PK; a re-run of the same made_on overwrites."""
    from gasbalance_core.models import Forecast

    payload: list[dict[str, Any]] = []
    for r in rows:
        sid = code_to_id.get(str(r["series_code"]))
        if sid is None:
            continue  # series not in the dictionary; skip
        payload.append(
            {
                "series_id": sid,
                "target_date": r["target_date"],
                "scenario": r["scenario"],
                "model_run_id": r["model_run_id"],
                "made_on": r["made_on"],
                "value": float(r["value"]),
                "run_id": run_id,
            }
        )
    if not payload:
        return 0

    # Last-wins dedup on the 5-col PK: two rows with the same key in one INSERT would trip
    # ON CONFLICT's "cannot affect row a second time" (mirrors etl/load/upsert.py).
    pk = ("series_id", "target_date", "scenario", "model_run_id", "made_on")
    payload = list({tuple(r[k] for k in pk): r for r in payload}.values())
    batch = max(1, _PG_MAX_PARAMS // len(payload[0]))  # keep rows*cols under the param cap

    written = 0
    for i in range(0, len(payload), batch):
        chunk = payload[i : i + batch]
        stmt = pg_insert(Forecast).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=list(pk),
            set_={"value": stmt.excluded.value, "run_id": stmt.excluded.run_id},
        )
        session.execute(stmt)
        written += len(chunk)
    return written


def publish_forecasts(
    rows: Sequence[Mapping[str, Any]], scenarios: Iterable[str]
) -> dict[str, int]:
    """Open a forecast_run, ensure scenarios, upsert all rows, close the run. One transaction.

    Returns `{"run_id": ..., "rows": n}`. On error the run is marked failed and re-raised.
    """
    from gasbalance_core.db import SessionLocal

    with SessionLocal() as session:
        ensure_scenarios(session, scenarios)
        run_id = open_forecast_run(session)
        try:
            code_to_id = series_ids(session, [str(r["series_code"]) for r in rows])
            written = upsert_forecasts(session, rows, run_id, code_to_id)
            close_forecast_run(session, run_id, "success", f"{written} forecast rows")
            session.commit()
        except Exception as exc:
            session.rollback()
            # exc.orig is the concise psycopg message; str(exc) drags in the full param dump.
            msg = str(getattr(exc, "orig", None) or exc)
            log.error("publish_forecasts failed: %s", msg)
            close_forecast_run(session, run_id, "failed", msg[:500])
            session.commit()
            raise
    return {"run_id": run_id, "rows": written}
