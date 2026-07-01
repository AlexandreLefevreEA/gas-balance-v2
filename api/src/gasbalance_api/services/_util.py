"""Small shared helpers for the read services."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from gasbalance_core.models import Series

MAX_CODES = 200


def split_codes(raw: str) -> list[str]:
    """Parse the `codes` csv: strip, drop blanks, dedupe (order-preserving), cap length."""
    codes = list(dict.fromkeys(c.strip() for c in raw.split(",") if c.strip()))
    if not codes:
        raise HTTPException(status_code=422, detail="codes: at least one code required")
    if len(codes) > MAX_CODES:
        raise HTTPException(status_code=422, detail=f"codes: too many (max {MAX_CODES})")
    return codes


def resolve_ids(session: Session, codes: list[str]) -> dict[int, str]:
    """`{series_id: code}` for the codes that exist. Unknown codes are silently dropped
    (batch reads return what's found rather than 404 on a partial set)."""
    rows = session.execute(select(Series.id, Series.code).where(Series.code.in_(codes))).all()
    return {int(sid): str(code) for sid, code in rows}
