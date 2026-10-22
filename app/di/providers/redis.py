"""Провайдер Redis: один клиент на приложение.

Таймауты: зависший Redis не должен держать запрос. Клиент ленив —
соединение открывается на первой команде.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from redis.asyncio import Redis

from app.config import Settings


class RedisProvider(Provider):
    """Клиент Redis в APP-скоупе, закрывается при закрытии контейнера."""

    scope = Scope.APP

    @provide
    async def redis(self, settings: Settings) -> AsyncIterator[Redis]:
        """Создать клиент из `REDIS_URL` и закрыть его на выходе APP-скоупа.

        Args:
            settings: Настройки с `redis_url`.

        Yields:
            Клиент `redis.asyncio.Redis`.

        Raises:
            RuntimeError: `REDIS_URL` не задан (fail-fast на старте).
        """
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is not configured")
        client = Redis.from_url(
            settings.redis_url,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            health_check_interval=30,
        )
        try:
            yield client
        finally:
            await client.aclose()
