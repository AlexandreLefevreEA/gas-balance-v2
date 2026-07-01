"""Series-catalog response shape."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SeriesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    category: str | None
    sub_group: str | None
    area: str | None
    unit: str
    source: str
    is_derived: bool
    is_active: bool
