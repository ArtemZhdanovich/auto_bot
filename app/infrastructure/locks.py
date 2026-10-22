"""Redis-реализация порта блокировок: `SET NX EX` + удаление ключа."""

from __future__ import annotations

from redis.asyncio import Redis


class RedisLock:
    """Именованный лок с TTL (cooldown и защита от параллельного ремонта)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def acquire(self, key: str, ttl_sec: int) -> bool:
        """Занять ключ на `ttl_sec`; False — ключ уже занят."""
        return bool(await self._redis.set(key, "1", nx=True, ex=ttl_sec))

    async def release(self, key: str) -> None:
        """Снять ключ (идемпотентно)."""
        await self._redis.delete(key)
