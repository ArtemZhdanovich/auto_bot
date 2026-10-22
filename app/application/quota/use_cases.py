"""Квота AI-подбора (ТЗ п. 8.7 + фидбек демо: «1 сообщение ≠ 1 поиск»).

Метрика — не сообщение, а **выдача результата**:
- preview_search вернул варианты → 1 поиск;
- finalize_search (полный поиск по источникам) → 1 поиск.

Сообщения-уточнения, вопросы, пустые выдачи — бесплатны: на демо человек
может свободно формулировать запрос диалогом, лимит тратится только когда
агент реально показал варианты или запустил полный поиск.

Счётчик — в `QuotaStore` с суточным ключом `ai_quota:{user_id}:{YYYYMMDD}`;
TTL ставится при создании ключа, поэтому окно не растягивается повторами.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.application.dialog.ports import QuotaStore

logger = logging.getLogger(__name__)

_DAY_SEC = 86400


def quota_key(user_id: int, now: datetime | None = None) -> str:
    """Ключ суточного счётчика: `ai_quota:{user_id}:{YYYYMMDD}`."""
    return f"ai_quota:{user_id}:{(now or datetime.now(UTC)):%Y%m%d}"


async def quota_used(store: QuotaStore, user_id: int) -> int:
    """Сколько поисков списано за сегодня."""
    return await store.get_int(quota_key(user_id))


async def quota_consume(store: QuotaStore, user_id: int, delta: int = 1) -> int:
    """Изменить счётчик на delta (за выдачу результата). Возвращает итог.

    `delta=-1` — компенсация: поиск упал, результат не выдан, квота не
    должна списываться.
    """
    key = quota_key(user_id)
    used = await store.add(key, delta)
    if used == delta:  # ключ только что создан — задаём суточное окно
        await store.expire(key, _DAY_SEC)
    logger.info("ai_quota_consume: user_id=%s used=%s", user_id, used)
    return used


async def quota_allow(store: QuotaStore, user_id: int, limit: int | None) -> bool:
    """limit=None — без лимита (business)."""
    return limit is None or await quota_used(store, user_id) < limit
