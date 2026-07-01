"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from gasbalance_core.db import SessionLocal


def get_db() -> Iterator[Session]:
    """Yield a request-scoped read session; always closed. Reuses core's engine
    (schema/search_path already set by its connect listener)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Annotated dep (avoids a call in the arg default — keeps ruff B008 quiet).
DbDep = Annotated[Session, Depends(get_db)]
