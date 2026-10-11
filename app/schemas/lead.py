"""Схемы лидов (ТЗ: обращение к менеджеру по объявлению)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LeadStatus = Literal["new", "in_progress", "closed", "rejected"]


class LeadCreateIn(BaseModel):
    """POST /api/leads — бот/Mini App создаёт обращение к менеджеру."""

    user_id: int
    listing_id: int | None = None
    source: str | None = None  # bot / miniapp / web
    comment: str | None = Field(default=None, max_length=2000)


class LeadStatusIn(BaseModel):
    """PATCH /api/leads/{id} — менеджер меняет статус лида."""

    status: LeadStatus
    manager_id: int | None = None
    comment: str | None = Field(default=None, max_length=2000)


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