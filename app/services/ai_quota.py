"""Квота AI-подбора (ТЗ п. 8.7 + фидбек демо: «1 сообщение ≠ 1 поиск»).

Метрика — не сообщение, а **выдача результата**:
- preview_search вернул варианты → 1 поиск;
- finalize_search (полный поиск по источникам) → 1 поиск.

Сообщения-уточнения, вопросы, пустые выдачи — бесплатны: на демо человек
может свободно формулировать запрос диалогом, лимит тратится только когда
агент реально показал варианты или запустил полный поиск.

Счётчик в Redis с суточным ключом: `ai_quota:{user_id}:{YYYYMMDD}`.
"""

import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_DAY_SEC = 86400


def _key(user_id: int) -> str:
    return f"ai_quota:{user_id}:{datetime.now(UTC):%Y%m%d}"


async def quota_used(redis: Redis, user_id: int) -> int:
    """Сколько поисков списано за сегодня."""
    raw = await redis.get(_key(user_id))
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def quota_consume(redis: Redis, user_id: int) -> int:
    """Списать 1 поиск (за выдачу результата). Возвращает израсходованное."""
    key = _key(user_id)
    used = int(await redis.incr(key))
    if used == 1:
        await redis.expire(key, _DAY_SEC)
    logger.info("ai_quota_consume: user_id=%s used=%s", user_id, used)
    return used


async def quota_allow(redis: Redis, user_id: int, limit: int | None) -> bool:
    """limit=None — без лимита (business)."""
    return limit is None or await quota_used(redis, user_id) < limit
