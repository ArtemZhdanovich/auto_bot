"""Фасад квоты AI-подбора (обратная совместимость).

Сценарий (ключ, суточное окно, allow/consume) — `application/quota/`,
счётчики — `infrastructure/cache/quota.py`. Здесь только сборка адаптера
поверх переданного клиента Redis.
"""

from typing import Any

from app.application.quota.use_cases import (
    quota_allow as _quota_allow,
    quota_consume as _quota_consume,
    quota_key,
    quota_used as _quota_used,
)
from app.infrastructure.cache.quota import RedisQuotaStore

__all__ = ["quota_allow", "quota_consume", "quota_key", "quota_used"]


async def quota_used(redis: Any, user_id: int) -> int:
    """Сколько поисков списано за сегодня."""
    return await _quota_used(RedisQuotaStore(redis), user_id)


async def quota_consume(redis: Any, user_id: int, delta: int = 1) -> int:
    """Списать поиск (delta=-1 — компенсация). Возвращает израсходованное."""
    return await _quota_consume(RedisQuotaStore(redis), user_id, delta)


async def quota_allow(redis: Any, user_id: int, limit: int | None) -> bool:
    """limit=None — без лимита (business)."""
    return await _quota_allow(RedisQuotaStore(redis), user_id, limit)
