"""Схемы лидов (ТЗ: обращение к менеджеру по объявлению)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LeadStatus = Literal["new", "in_progress", "closed", "rejected"]


class LeadCreateIn(BaseModel):
    """POST /api/leads — бот/Mini App создаёт обращение к менеджеру."""

    user_id: int
    listing_id: int | None = None
    source: str | None = None  # bot / miniapp / web
    comment: str | None = Field(default=None, max_length=2000)


class LeadStatusIn(BaseModel):
    """PATCH /api/leads/{id} — частичное обновление лида.

    Все поля опциональны, но хотя бы одно должно быть передано: иначе
    PATCH превращается в no-op с записью в audit-лог.
    """

    status: LeadStatus | None = None
    manager_id: int | None = None
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "LeadStatusIn":
        if self.status is None and self.manager_id is None and self.comment is None:
            raise ValueError("at least one field must be provided")
        return self


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    listing_id: int | None
    source: str | None
    status: str
    manager_id: int | None
    comment: str | None
    created_at: datetime