"""`etl run <source>` / `etl run all` — the only ETL entrypoint.

Source-agnostic pipeline, per connector, wrapped in an `etl_run` audit row:

    since = None (full) | last obs date (incremental)
    fetch(since) -> to_canonical -> VALIDATE (Pandera) -> sync_series -> upsert

Validation failure blocks the load (nothing written, run marked failed, non-zero exit).
Connectors are isolated: one failing source never blocks the others.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import logging
import os
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
        # Connectors may declare their own sink (e.g. hourly covariates); default is
        # the daily observation upsert. See ADR 0008.
        load_fn = getattr(conn, "load", upsert_observations)
        loaded = load_fn(session, df, run.run_id, code_to_id)
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


def _run_phase(names: list[str], jobs: int) -> int:
    """Run the given connectors concurrently (own session + asyncio loop each); return # failed.

    Connectors share no mutable state, so threading is safe; each failure is isolated (logged and
    recorded in `etl_run`) and never blocks the others. The pool join (context exit) is the barrier
    the transform phase relies on — every raw connector has committed before transforms read.
    """
    if not names:
        return 0
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_run_one, name, REGISTRY[name]): name for name in names}
        for fut in concurrent.futures.as_completed(futures):
            try:
                fut.result()
            except Exception:  # already logged + recorded in etl_run; isolate the others
                failed += 1
    return failed


def _prune_one(name: str) -> int:
    """Apply a connector's retention policy, if it declares one (`prune` hook)."""
    conn = REGISTRY[name]
    prune = getattr(conn, "prune", None)
    if prune is None:
        log.warning("%s has no retention policy; nothing to prune", name)
        return 0
    with SessionLocal() as session:
        deleted = prune(session)
        session.commit()
    log.info("%s: pruned %d rows", name, deleted)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="etl")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run a connector (or 'all')")
    run_p.add_argument("source", help="connector name or 'all'")
    prune_p = sub.add_parser("prune", help="apply a connector's retention policy")
    prune_p.add_argument("source", help="connector name")
    args = parser.parse_args(argv)

    if args.cmd == "prune":
        if args.source not in REGISTRY:
            known = ", ".join(REGISTRY) or "(none)"
            parser.error(f"unknown source '{args.source}'. Known: {known}")
        return _prune_one(args.source)

    if args.source != "all" and args.source not in REGISTRY:
        known = ", ".join(REGISTRY) or "(none)"
        parser.error(f"unknown source '{args.source}'. Known: {known}")

    if args.source != "all":
        try:
            _run_one(args.source, REGISTRY[args.source])
        except Exception:  # already logged + recorded in etl_run
            return 1
        return 0

    # `run all`: raw connectors concurrently, then the transforms (they read what raw loaded — the
    # only dependency, ADR 0007). raw connectors are mutually independent (verified: none reads
    # another's data), so order within a phase is free.
    jobs = int(os.environ.get("ETL_JOBS", "6"))
    raw = [n for n in REGISTRY if not getattr(REGISTRY[n], "is_transform", False)]
    transforms = [n for n in REGISTRY if getattr(REGISTRY[n], "is_transform", False)]
    log.info(
        "etl run all: %d raw connectors (<=%d concurrent), then %d transform(s)",
        len(raw),
        jobs,
        len(transforms),
    )
    failed = _run_phase(raw, jobs)
    failed += _run_phase(transforms, jobs)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
