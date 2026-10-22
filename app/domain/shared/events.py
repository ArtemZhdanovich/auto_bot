"""Доменные события: факты, которые уже произошли.

Событие неизменяемо и не знает, кто и как его опубликует. Транспорт
(брокер) и outbox живут в инфраструктуре.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4


def now_utc() -> datetime:
    """Текущее время в UTC (единая точка для тестов)."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    """Базовое доменное событие.

    Attributes:
        event_id: Ключ идемпотентности публикации и обработки.
        occurred_at: Момент возникновения факта.
    """

    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=now_utc)
