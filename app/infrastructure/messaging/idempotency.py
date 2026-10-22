"""Redis-реализация порта `IdempotencyGuard`.

`SET NX EX` — одна атомарная команда: либо ключ наш (первая доставка), либо
он уже занят (повтор). `release` нужен только на пути ошибки: упавшее
сообщение должно получить повторную доставку, поэтому ключ снимается.
"""

from __future__ import annotations

from redis.asyncio import Redis


class RedisIdempotencyGuard:
    """Отсекает повторную обработку сообщения по ключу в Redis."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def acquire(self, key: str, ttl_sec: int) -> bool:
        """Занять ключ на `ttl_sec` секунд.

        Args:
            key: Полное имя ключа (с префиксом вида `idem:...`).
            ttl_sec: Время жизни ключа; повтор в этом окне будет отброшен.

        Returns:
            True — ключ заняли мы (первая доставка), False — ключ уже занят.
        """
        return bool(await self._redis.set(key, "1", nx=True, ex=ttl_sec))

    async def release(self, key: str) -> None:
        """Снять ключ, чтобы сообщение можно было обработать повторно.

        Args:
            key: Имя ключа, занятого в `acquire`.
        """
        await self._redis.delete(key)
