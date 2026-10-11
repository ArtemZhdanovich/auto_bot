"""Пул резидентских прокси в Redis (ТЗ п. 6.3).

Ключи: `proxy:good`, `proxy:bad`, `proxy:cooldown`.
"""

import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

GOOD_KEY = "proxy:good"
BAD_KEY = "proxy:bad"
COOLDOWN_KEY = "proxy:cooldown"


class ProxyPool:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self) -> str | None:
        """Случайный рабочий прокси или None (прямой запрос), если пул пуст."""
        proxy = await self._redis.srandmember(GOOD_KEY)
        return proxy.decode() if isinstance(proxy, bytes) else proxy

    async def add(self, *proxies: str) -> None:
        if proxies:
            await self._redis.sadd(GOOD_KEY, *proxies)

    async def mark_bad(self, proxy: str) -> None:
        await self._redis.srem(GOOD_KEY, proxy)
        await self._redis.sadd(BAD_KEY, proxy)
        logger.warning("proxy_marked_bad: %s", proxy)

    async def restore(self, proxy: str) -> None:
        """Вернуть прокси из bad/cooldown в good после успешного healthcheck."""
        await self._redis.srem(BAD_KEY, proxy)
        await self._redis.srem(COOLDOWN_KEY, proxy)
        await self._redis.sadd(GOOD_KEY, proxy)

    async def healthcheck_all(self) -> dict[str, int]:
        """Поддерживающая проверка: считает прокси по группам.

        Полноценный healthcheck выполняется Scheduler'ом (этап 8).
        """
        return {
            "good": await self._redis.scard(GOOD_KEY),
            "bad": await self._redis.scard(BAD_KEY),
            "cooldown": await self._redis.scard(COOLDOWN_KEY),
        }
