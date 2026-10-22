"""`CachePort` поверх `redis.asyncio.Redis`.

Декодирует значения в `str`: redis может отдавать `bytes` (decode_responses=False),
а сценариям приложения нужен текст (переводы, счётчики).
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str | None:
    """Значение Redis → `str | None` (bytes декодируются)."""
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


class RedisCache:
    """Тонкая обёртка над клиентом Redis (get/set/incr/expire)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, key: str) -> str | None:
        return _text(await self._client.get(key))

    async def get_many(self, keys: list[str]) -> list[str | None]:
        if not keys:
            return []
        return [_text(value) for value in await self._client.mget(*keys)]

    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None:
        if ttl_sec is None:
            await self._client.set(key, value)
        else:
            await self._client.set(key, value, ex=ttl_sec)

    async def set_if_absent(self, key: str, value: str, ttl_sec: int) -> bool:
        """`SET NX EX`: `True`, если ключ занял этот вызов."""
        return bool(await self._client.set(key, value, nx=True, ex=ttl_sec))

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def incr(self, key: str) -> int:
        return int(await self._client.incr(key))

    async def expire(self, key: str, ttl_sec: int) -> None:
        await self._client.expire(key, ttl_sec)
