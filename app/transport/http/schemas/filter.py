"""Схемы фильтров пользователя (этап 5, ТЗ п. 9.2.3)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.search.criteria import SearchCriteria


class FilterCreate(BaseModel):
    name: str | None = None
    criteria: SearchCriteria = Field(default_factory=SearchCriteria)
    notify_mode: Literal["instant", "digest", "off"] = "digest"
    quiet_hours: str | None = None  # "23:00-08:00"


class FilterUpdate(BaseModel):
    name: str | None = None
    criteria: SearchCriteria | None = None
    notify_mode: Literal["instant", "digest", "off"] | None = None
    quiet_hours: str | None = None


class FilterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    criteria: dict
    notify_mode: str
    quiet_hours: str | None
    created_at: datetime
    updated_at: datetime | None
