"""`QuotaStore` поверх Redis: суточные счётчики AI-квоты.

Ключ и TTL задаёт сценарий (`application/quota/use_cases.py`); адаптер
умеет только читать и менять счётчик.
"""

from __future__ import annotations

from typing import Any


class RedisQuotaStore:
    """Счётчики квоты на `INCR`/`INCRBY`/`DECR` с TTL."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get_int(self, key: str) -> int:
        raw = await self._client.get(key)
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    async def add(self, key: str, delta: int) -> int:
        """Изменить счётчик на delta (отрицательная — компенсация списания)."""
        if delta == 1:
            return int(await self._client.incr(key))
        if delta == -1:
            return int(await self._client.decr(key))
        return int(await self._client.incrby(key, delta))

    async def expire(self, key: str, ttl_sec: int) -> None:
        await self._client.expire(key, ttl_sec)
