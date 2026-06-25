"""`etl run <source>` / `etl run all` — the only ETL entrypoint.

Source-agnostic pipeline, per connector, wrapped in an `etl_run` audit row:

    since = None (full) | last obs date (incremental)
    fetch(since) -> to_canonical -> VALIDATE (Pandera) -> sync_series -> upsert

Validation failure blocks the load (nothing written, run marked failed, non-zero exit).
Connectors are isolated: one failing source never blocks the others.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from typing import Any

import pandera.errors as pa_errors

from gasbalance_core.db import SessionLocal
from gasbalance_core.models import EtlRun
from gasbalance_etl.connectors import REGISTRY
from gasbalance_etl.load.upsert import sync_series, upsert_observations

log = logging.getLogger("gasbalance_etl")


def _run_one(name: str, conn: Any) -> int:
    """Run one connector end-to-end. Returns rows loaded; raises after marking failure."""
    session = SessionLocal()
    run = EtlRun(source=name, status="running")
    session.add(run)
    session.commit()  # persist the 'running' row and get run_id
    try:
        df = conn.to_canonical(conn.fetch(None))
        rows_in = len(df)
        conn.schema.validate(df, lazy=True)  # raises on bad data -> blocks the load
        code_to_id = sync_series(session, conn.series_dict(), name)
        loaded = upsert_observations(session, df, run.run_id, code_to_id)
        run.status = "success"
        run.rows_in = rows_in
        run.rows_loaded = loaded
        run.finished_at = dt.datetime.now(dt.UTC)
        session.commit()
        log.info("%s: loaded %d/%d rows (run %d)", name, loaded, rows_in, run.run_id)
        return loaded
    except Exception as exc:
        session.rollback()
        run.status = "failed"
        run.finished_at = dt.datetime.now(dt.UTC)
        if isinstance(exc, pa_errors.SchemaErrors):
            run.message = f"validation failed: {len(exc.failure_cases)} bad rows"
            log.error("%s: VALIDATION blocked load:\n%s", name, exc.failure_cases.head(20))
        else:
            run.message = str(exc)[:1000]
            log.error("%s: FAILED — %s", name, exc)
        session.commit()
        raise
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="etl")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run a connector (or 'all')")
    run_p.add_argument("source", help="connector name or 'all'")
    args = parser.parse_args(argv)

    if args.source != "all" and args.source not in REGISTRY:
        known = ", ".join(REGISTRY) or "(none)"
        parser.error(f"unknown source '{args.source}'. Known: {known}")

    names = list(REGISTRY) if args.source == "all" else [args.source]
    failed = 0
    for name in names:
        try:
            _run_one(name, REGISTRY[name])
        except Exception:  # already logged + recorded in etl_run; isolate the others
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
