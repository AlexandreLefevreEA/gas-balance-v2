"""Alembic environment — env-agnostic.

Reads the target DB + schema from gasbalance_core.config, so `alembic upgrade head`
applies to whatever environment the config points at. A guard-rail refuses any
non-local host unless APP_ENV=prod is explicitly set.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import make_url

import gasbalance_core.models  # noqa: F401  -- register all tables on the metadata
from gasbalance_core.config import get_settings
from gasbalance_core.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
SCHEMA = settings.db_schema
target_metadata = Base.metadata


def _guard_target() -> None:
    url = make_url(settings.database_url)
    host = (url.host or "localhost").lower()
    if host not in {"localhost", "127.0.0.1", "::1"} and settings.app_env != "prod":
        raise RuntimeError(
            f"Refusing migrations against non-local host {host!r} with "
            f"APP_ENV={settings.app_env!r}. Set APP_ENV=prod to confirm."
        )
    print(f"[alembic] host={host} db={url.database} schema={SCHEMA} app_env={settings.app_env}")


def run_migrations_offline() -> None:
    _guard_target()
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=SCHEMA,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _guard_target()
    connectable = create_engine(settings.database_url, poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
        )
        with context.begin_transaction():
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
