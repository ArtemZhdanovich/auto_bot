"""DTO фильтра пользователя для прикладного слоя (без ORM и HTTP)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FilterRecord:
    """Сохранённый фильтр в контракте `FilterOut`."""

    id: int
    name: str | None
    criteria: dict
    notify_mode: str
    quiet_hours: str | None
    created_at: datetime
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class FilterDraft:
    """Данные нового фильтра."""

    name: str | None
    criteria: dict
    notify_mode: str = "digest"
    quiet_hours: str | None = None


@dataclass(frozen=True, slots=True)
class FilterPatch:
    """Частичное обновление: `None` — поле не меняется."""

    name: str | None = None
    criteria: dict | None = None
    notify_mode: str | None = None
    quiet_hours: str | None = None
