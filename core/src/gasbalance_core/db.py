"""SQLAlchemy engine, session factory, and the declarative Base.

All ORM tables inherit `Base.metadata`, which is bound to the configured schema
(default `gas_balance`), and every connection sets `search_path` to it so
unqualified queries resolve there.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from gasbalance_core.config import get_settings

_settings = get_settings()


class Base(DeclarativeBase):
    metadata = MetaData(schema=_settings.db_schema)


# Pool sized for parallel `etl run all` (ETL_JOBS connector threads, each its own session) plus
# headroom for the audit-row + transform sessions; harmless for single-threaded callers.
_jobs = int(os.environ.get("ETL_JOBS", "6"))
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=max(10, _jobs + 5),
    max_overflow=20,
    future=True,
)


@event.listens_for(engine, "connect")
def _set_search_path(dbapi_connection: Any, _record: Any) -> None:
    # db_schema is trusted config (not user input).
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f"SET search_path TO {_settings.db_schema}")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
