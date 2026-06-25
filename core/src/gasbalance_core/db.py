"""SQLAlchemy engine, session factory, and the declarative Base.

All ORM tables inherit `Base.metadata`, which is bound to the configured schema
(default `gas_balance`), and every connection sets `search_path` to it so
unqualified queries resolve there.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from gasbalance_core.config import get_settings

_settings = get_settings()


class Base(DeclarativeBase):
    metadata = MetaData(schema=_settings.db_schema)


engine = create_engine(_settings.database_url, pool_pre_ping=True, future=True)


@event.listens_for(engine, "connect")
def _set_search_path(dbapi_connection: Any, _record: Any) -> None:
    # db_schema is trusted config (not user input).
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f"SET search_path TO {_settings.db_schema}")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
