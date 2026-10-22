"""Диалоги: FAQ-кэш (п. 7.8), DialogState в Redis (п. 7.6), финализация.

Ключи Redis:
- `dialog:{user_id}` — DialogState, TTL 24 часа;
- `faq:{topic}` — кэш ответов базы знаний, TTL 24 часа.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.db.models import DialogSession, UserFilter
from app.domain.search.criteria import SearchCriteria
from app.domain.dialog.state import DialogState, Message

logger = logging.getLogger(__name__)

# TECHDEBT 4.9: потолок истории сообщений в DialogState (Redis-значение
# не должно расти неограниченно). Всё, что старше, тихо отбрасывается.
MAX_STATE_MESSAGES = 50


def dialog_key(user_id: int) -> str:
    return f"dialog:{user_id}"


# --- FAQ (ТЗ п. 7.8) ----------------------------------------------------

from app.domain.dialog.faq import FAQ


async def answer_faq(redis: Redis, topic: str) -> str | None:
    cached = await redis.get(f"faq:{topic}")
    if cached:
        return cached.decode() if isinstance(cached, bytes) else cached
    answer = FAQ.get(topic)
    if answer:
        await redis.setex(f"faq:{topic}", get_settings().faq_cache_ttl_sec, answer)
    return answer


# --- хранилище DialogState (ТЗ п. 7.6) -----------------------------------


from app.domain.dialog.state import new_state


class DialogStore:
    def __init__(self, redis: Redis, ttl_sec: int | None = None) -> None:
        self._redis = redis
        self._ttl = ttl_sec or get_settings().dialog_ttl_sec

    async def load(self, user_id: int) -> DialogState | None:
        raw = await self._redis.get(dialog_key(user_id))
        if not raw:
            return None
        try:
            return DialogState.model_validate_json(raw)
        except Exception:
            logger.exception("dialog_state_corrupted: user_id=%s", user_id)
            return None

    async def save(self, state: DialogState) -> None:
        # TECHDEBT 4.9: история в state ограничена — без капа Redis-значение
        # растёт неограниченно у «долгоживущих» диалогов.
        state.messages = state.messages[-MAX_STATE_MESSAGES:]
        state.updated_at = datetime.now(UTC)
        await self._redis.setex(
            dialog_key(state.user_id), self._ttl, state.model_dump_json()
        )

    async def delete(self, user_id: int) -> None:
        await self._redis.delete(dialog_key(user_id))


# --- архивация и финализация (ТЗ п. 7.6) ---------------------------------


async def archive_session(session: AsyncSession, state: DialogState) -> None:
    """Сохранить/обновить сессию в Postgres — история диалога живёт дольше
    Redis-state: её можно посмотреть спустя время (п. «сессии сохраняются»)."""
    existing = await session.get(DialogSession, uuid.UUID(state.session_id))
    if existing is None:
        existing = DialogSession(id=uuid.UUID(state.session_id))
        session.add(existing)
    existing.user_id = state.user_id
    existing.status = state.status
    existing.criteria = state.criteria.model_dump(mode="json")
    existing.turn_count = state.turn_count
    existing.messages = [m.model_dump(mode="json") for m in state.messages]
    existing.updated_at = datetime.now(UTC)
    if existing.created_at is None:
        existing.created_at = state.created_at
    if state.status in ("done", "searching"):
        existing.finished_at = datetime.now(UTC)


async def restore_dialog(
    user_id: int, *, session: AsyncSession, store: DialogStore
) -> dict[str, Any] | None:
    """Поднять последний диалог из архива (п. «посмотреть диалог спустя время»).

    Активный Redis-state не трогаем (restored=False, reason=active).
    Восстановленная сессия получает новый session_id — критерии и историю
    переносим, диалог продолжается.
    """
    active = await store.load(user_id)
    if active is not None:
        return {"restored": False, "reason": "active"}
    row = (
        await session.execute(
            select(DialogSession)
            .where(DialogSession.user_id == user_id)
            .order_by(
                DialogSession.updated_at.desc().nulls_last(),
                DialogSession.created_at.desc().nulls_last(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or not row.messages:
        return None
    state = new_state(user_id)
    state.criteria = SearchCriteria(**(row.criteria or {}))
    state.messages = [Message(**m) for m in row.messages]
    state.turn_count = row.turn_count or len(state.messages)
    state.status = "collecting"
    await store.save(state)
    logger.info("dialog_restored: user_id=%s from=%s", user_id, row.id)
    return {
        "restored": True,
        "session_id": state.session_id,
        "criteria": row.criteria or {},
        "messages": row.messages,
    }


async def cleanup_expired_dialogs(session: AsyncSession) -> int:
    """Снос архивных диалогов, молчащих сутки с последнего сообщения
    (п. «диалоги сносить через сутки»). Вызывается cron-джобой воркера."""
    cutoff = datetime.now(UTC) - timedelta(
        hours=ceil(get_settings().dialog_ttl_sec / 3600)
    )
    result = await session.execute(
        delete(DialogSession).where(
            or_(
                DialogSession.updated_at < cutoff,
                and_(
                    DialogSession.updated_at.is_(None),
                    DialogSession.created_at < cutoff,
                ),
            )
        )
    )
    await session.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    if deleted:
        logger.info("dialog_cleanup: deleted=%s cutoff=%s", deleted, cutoff)
    return deleted


async def reset_dialog(
    user_id: int, *, session: AsyncSession, store: DialogStore
) -> None:
    """/reset → архив старой сессии, удаление состояния (п. 7.6)."""
    state = await store.load(user_id)
    if state:
        state.status = "done"
        await archive_session(session, state)
    await store.delete(user_id)
    await session.commit()


async def finalize_dialog(
    user_id: int,
    *,
    session: AsyncSession,
    store: DialogStore,
    enqueue: Callable[[SearchCriteria], Awaitable[list[str]]],
    criteria: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Финализация (ТЗ п. 7.13): фиксация критериев → фильтр с уведомлениями
    (п. 8.4) → задачи парсинга по источникам → архивация → сброс состояния."""
    state = await store.load(user_id)
    if state is None:
        state = new_state(user_id)
    if criteria:
        state.criteria = SearchCriteria(**criteria)
    state.status = "searching"

    # фильтр, по которому дальше работают уведомления
    session.add(
        UserFilter(
            user_id=user_id,
            name=f"Поиск от {datetime.now(UTC):%d.%m.%Y %H:%M}",
            criteria=state.criteria.model_dump(mode="json"),
        )
    )
    sources = await enqueue(state.criteria)
    await archive_session(session, state)
    await session.commit()
    await store.delete(user_id)

    logger.info("dialog_finalized: user_id=%s sources=%s", user_id, sources)
    return {"criteria": state.criteria.model_dump(mode="json"), "sources": sources}
