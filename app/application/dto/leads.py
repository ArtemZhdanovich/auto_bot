"""DTO лида: представление для транспорта и частичное обновление.

`LeadView` повторяет поля `LeadOut` 1:1 — сценарий отдаёт его наружу, а
транспорт маппит в Pydantic-схему. `LeadPatch` отличает «поле не передано»
(`None`) от «передано новое значение»: без этого PATCH затирал бы статус.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LeadView:
    """Лид в объёме ответа API (без ORM-объекта)."""

    id: int
    user_id: int
    listing_id: int | None
    source: str | None
    status: str
    manager_id: int | None
    comment: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LeadPatch:
    """Частичное обновление лида: `None` означает «не менять»."""

    status: str | None = None
    manager_id: int | None = None
    comment: str | None = None
