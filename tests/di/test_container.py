"""Сборка Dishka-контейнера: подмена БД/Redis фейками и сценарий toggle.

Проверяем то, что нельзя увидеть по коду: контейнер собирается, `Settings`
живёт в APP-скоупе, а `ToggleFavorite` резолвится в REQUEST-скоупе и
выполняется на активном объявлении поверх фейковой сессии.
"""

from __future__ import annotations

from typing import Any

import pytest
from dishka import Provider, Scope, provide
from dishka.exceptions import NoFactoryError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.favorites.use_cases import ToggleFavorite
from app.application.catalog.dto import ToggleFavoriteCommand
from app.config import Settings
from app.di.container import make_container
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork


class FakeResult:
    """Результат `execute`: объявление активно, toggle вернул «добавлено»."""

    def all(self) -> list[Any]:
        """Пустая выборка (страницы избранного в этих тестах нет)."""
        return []

    def scalar(self) -> bool:
        """`exists_active` → объявление существует и активно."""
        return True

    def scalar_one(self) -> bool:
        """`toggle` → запись добавлена."""
        return True


class FakeSession:
    """Мини-сессия SQLAlchemy: считает запросы и коммиты."""

    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.flushes = 0
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        """Запомнить запрос и вернуть «успешный» результат."""
        self.statements.append((statement, params))
        return FakeResult()

    async def flush(self) -> None:
        """Заглушка flush (UoW вызывает перед commit)."""
        self.flushes += 1

    async def commit(self) -> None:
        """Заглушка commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Заглушка rollback."""
        return None

    async def close(self) -> None:
        """Заглушка close."""
        return None


class FakeRedis:
    """Заглушка клиента Redis: сценарию toggle он не нужен."""


class FakeDatabaseProvider(Provider):
    """Подмена `DatabaseProvider`: сессия и UoW на `FakeSession`."""

    @provide(scope=Scope.REQUEST, provides=AsyncSession)
    def session(self) -> FakeSession:
        """Одна фейковая сессия на запрос.

        Returns:
            `FakeSession` под ключом `AsyncSession`.
        """
        return FakeSession()

    @provide(scope=Scope.REQUEST)
    def unit_of_work(self, session: AsyncSession) -> SqlAlchemyUnitOfWork:
        """Настоящий UoW поверх фейковой сессии.

        Args:
            session: Фейковая сессия запроса.

        Returns:
            `SqlAlchemyUnitOfWork` без outbox и publisher-а.
        """
        return SqlAlchemyUnitOfWork(session)


class FakeRedisProvider(Provider):
    """Подмена `RedisProvider`: клиент не поднимается."""

    @provide(scope=Scope.APP, provides=Redis)
    def redis(self) -> FakeRedis:
        """Заглушка клиента Redis.

        Returns:
            `FakeRedis` под ключом `redis.asyncio.Redis`.
        """
        return FakeRedis()


def _make_fake_container(*extra: Provider):
    """Контейнер на фейках БД/Redis вместо реальных провайдеров.

    Args:
        *extra: Дополнительные провайдеры поверх фейковых.

    Returns:
        `AsyncContainer` с подменённой инфраструктурой.
    """
    return make_container(FakeDatabaseProvider(), FakeRedisProvider(), *extra)


async def test_settings_resolves_in_app_scope() -> None:
    """`Settings` доступен без открытия REQUEST-скоупа, сценарий — нет."""
    container = _make_fake_container()
    try:
        settings = await container.get(Settings)
        assert isinstance(settings, Settings)

        # REQUEST-зависимость из APP-контейнера не резолвится: сценарий
        # не «утекает» в долгоживущий скоуп.
        with pytest.raises(NoFactoryError):
            await container.get(ToggleFavorite)

        async with container() as request:
            from_request = await request.get(Settings)
        assert from_request is settings, "Settings должен быть один на контейнер"
    finally:
        await container.close()


async def test_toggle_favorite_executes_on_active_listing() -> None:
    """`ToggleFavorite` резолвится в REQUEST и выполняется на активном id."""
    container = _make_fake_container()
    try:
        async with container() as request:
            session = await request.get(AsyncSession)
            use_case = await request.get(ToggleFavorite)

            state = await use_case.execute(
                ToggleFavoriteCommand(user_id=910001, listing_id=42)
            )

            assert state.favorited is True
            # exists_active + атомарный toggle, затем один commit UoW.
            assert len(session.statements) == 2
            assert session.flushes == 1
            assert session.commits == 1
    finally:
        await container.close()


async def test_default_container_assembles_real_providers() -> None:
    """Штатный набор провайдеров собирается и отдаёт engine/redis без сети.

    Соединения не открываются: `create_async_engine` и `Redis.from_url` ленивы,
    `close()` освобождает пул и клиент.
    """
    container = make_container()
    try:
        settings = await container.get(Settings)
        assert settings.database_url
        assert settings.redis_url

        engine = await container.get(AsyncEngine)
        assert engine.pool.size() == getattr(settings, "db_pool_size", 10)

        factory = await container.get(async_sessionmaker[AsyncSession])
        assert factory.kw["expire_on_commit"] is False

        assert isinstance(await container.get(Redis), Redis)
    finally:
        await container.close()
