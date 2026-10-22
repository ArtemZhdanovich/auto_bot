"""Transactional outbox: событие хранится в БД до публикации.

Таблица пишется в той же транзакции, что и бизнес-данные, поэтому
«данные без события» и «событие без данных» невозможны. Релей публикует
строки после commit (at-least-once).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models import Base


class OutboxMessage(Base):
    """Неопубликованное доменное событие."""

    __tablename__ = "outbox"
    __table_args__ = (
        # Релей ищет только неопубликованные — частичный индекс дешевле.
        Index(
            "idx_outbox_unpublished",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(PGUUID, unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(String(512))


class OutboxDAO:
    """Запись событий в outbox в рамках текущей сессии."""

    def __init__(self, session) -> None:
        self._session = session

    def add_all(self, events) -> None:
        """Добавить события в сессию (commit сделает UoW)."""
        from app.infrastructure.db.mappers import event_to_dict, routing_key_for

        self._session.add_all(
            [
                OutboxMessage(
                    event_id=event.event_id,
                    event_type=type(event).__name__,
                    routing_key=routing_key_for(event),
                    payload=event_to_dict(event)["payload"],
                    occurred_at=event.occurred_at,
                )
                for event in events
            ]
        )
