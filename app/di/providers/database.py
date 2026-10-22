"""Провайдер PostgreSQL: engine и фабрика сессий в APP, сессия и UoW в REQUEST.

Параметры пула и серверные таймауты повторяют `app/db/session.py`: пул
переживает запросы, сессия и Unit of Work — нет. `connect_args` —
asyncpg-специфичные ключи, как и в существующем модуле.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.infrastructure.db.outbox import OutboxDAO
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

# Серверные таймауты (мс): те же значения, что в `app/db/session.py`.
_STATEMENT_TIMEOUT_MS = "30000"
_LOCK_TIMEOUT_MS = "5000"
_IDLE_IN_TRANSACTION_TIMEOUT_MS = "60000"


class DatabaseProvider(Provider):
    """Собирает engine, фабрику сессий, сессию запроса и UoW."""

    scope = Scope.APP

    @provide
    async def engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        """Создать `AsyncEngine` и закрыть пул при закрытии контейнера.

        Args:
            settings: Настройки с `database_url` и pool-параметрами.

        Yields:
            Настроенный `AsyncEngine` (соединение не открывается до запроса).
        """
        engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=getattr(settings, "db_pool_size", 10),
            max_overflow=getattr(settings, "db_max_overflow", 20),
            pool_timeout=getattr(settings, "db_pool_timeout_sec", 10),
            pool_recycle=getattr(settings, "db_pool_recycle_sec", 1800),
            pool_use_lifo=True,
            connect_args={
                "timeout": 10,  # TCP-connect timeout
                "command_timeout": 30,  # client-side safety net
                "server_settings": {
                    "application_name": "autoselect-api",
                    "statement_timeout": _STATEMENT_TIMEOUT_MS,
                    "lock_timeout": _LOCK_TIMEOUT_MS,
                    "idle_in_transaction_session_timeout": (
                        _IDLE_IN_TRANSACTION_TIMEOUT_MS
                    ),
                },
            },
        )
        try:
            yield engine
        finally:
            await engine.dispose()

    @provide
    def session_factory(
        self, engine: AsyncEngine
    ) -> async_sessionmaker[AsyncSession]:
        """Фабрика сессий: `expire_on_commit=False` (см. `app/db/session.py`).

        Args:
            engine: Пул соединений приложения.

        Returns:
            Фабрика `AsyncSession` на этом engine.
        """
        return async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @provide(scope=Scope.REQUEST)
    async def session(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[AsyncSession]:
        """Сессия на время запроса: `close()` откатывает незакоммиченное.

        Args:
            factory: Фабрика сессий из APP-скоупа.

        Yields:
            `AsyncSession`, закрываемая вместе с REQUEST-скоупом.
        """
        async with factory() as session:
            yield session

    @provide(scope=Scope.REQUEST)
    def unit_of_work(self, session: AsyncSession) -> SqlAlchemyUnitOfWork:
        """UoW на сессии запроса: outbox выключен, публикация после commit.

        Args:
            session: Сессия текущего запроса.

        Returns:
            `SqlAlchemyUnitOfWork` с `OutboxDAO` и без publisher-а.
        """
        return SqlAlchemyUnitOfWork(
            session,
            OutboxDAO(session),
            publisher=None,
            use_outbox=False,
        )
