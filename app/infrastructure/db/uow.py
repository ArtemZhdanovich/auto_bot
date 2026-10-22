"""Unit of Work: одна транзакция на запрос или сообщение.

События либо пишутся в outbox в той же транзакции (`use_outbox=True`),
либо буферизуются и публикуются после успешного commit.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.shared.ports import EventPublisher
from app.domain.shared.events import DomainEvent
from app.infrastructure.db.mappers import routing_key_for
from app.infrastructure.db.outbox import OutboxDAO


class SqlAlchemyUnitOfWork:
    """Транзакционная граница прикладного слоя."""

    def __init__(
        self,
        session: AsyncSession,
        outbox: OutboxDAO | None = None,
        publisher: EventPublisher | None = None,
        *,
        use_outbox: bool = False,
    ) -> None:
        self._session = session
        self._outbox = outbox
        self._publisher = publisher
        self._use_outbox = use_outbox
        self._events: list[DomainEvent] = []

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    def collect(self, *events: DomainEvent) -> None:
        """Зарегистрировать события текущей транзакции."""
        self._events.extend(events)

    async def commit(self) -> None:
        """Зафиксировать данные и события.

        Raises:
            RuntimeError: Если включён outbox, но DAO не передан.
        """
        await self._session.flush()
        events, self._events = self._events, []
        if events:
            if self._use_outbox:
                if self._outbox is None:
                    raise RuntimeError("use_outbox=True requires OutboxDAO")
                self._outbox.add_all(events)
            elif self._publisher is not None:
                for event in events:
                    await self._publisher.publish(event, routing_key_for(event))

        await self._session.commit()

        # Строго после commit: иначе событие уйдёт по откатённой транзакции.
        if events and not self._use_outbox and self._publisher is not None:
            await self._publisher.flush()

    async def rollback(self) -> None:
        """Отменить транзакцию и забыть события."""
        self._events.clear()
        await self._session.rollback()


class SessionUnitOfWork:
    """Транзакционная граница без событий и outbox.

    Для сценариев, где нет доменных событий и сессией владеет вызывающий
    код (роутер, воркер): только commit/rollback, без flush и публикации.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> "SessionUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    async def commit(self) -> None:
        """Зафиксировать изменения сессии."""
        await self._session.commit()

    async def rollback(self) -> None:
        """Отменить транзакцию."""
        await self._session.rollback()
