"""Диалоги: FAQ-кэш (п. 7.8), DialogState в Redis (п. 7.6), финализация.

Ключи Redis:
- `dialog:{user_id}` — DialogState, TTL 24 часа;
- `faq:{topic}` — кэш ответов базы знаний, TTL 24 часа.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import DialogSession, UserFilter
from app.schemas.criteria import SearchCriteria
from app.schemas.dialog import DialogState

logger = logging.getLogger(__name__)


def dialog_key(user_id: int) -> str:
    return f"dialog:{user_id}"


# --- FAQ (ТЗ п. 7.8) ----------------------------------------------------

FAQ: dict[str, str] = {
    "customs_kr_ru": (
        "Растаможка из Кореи в РФ зависит от возраста, объёма и стоимости авто. "
        "Для авто 3–5 лет считается от объёма двигателя, старше 5 лет — от объёма "
        "и стоимости. Точный расчёт сделаю в калькуляторе — назовите модель, год "
        "и объём двигателя."
    ),
    "delivery_kr_vladivostok": (
        "Доставка Корея → Владивосток: 7–14 дней, морем, ~$800–1200 за авто. "
        "Дальше по РФ — автовоз или ж/д, зависит от города."
    ),
    "risks_auction": (
        "Основные риски аукционов: скрученный пробег, скрытые ДТП, торги вслепую. "
        "Мы проверяем аукционный лист и историю перед покупкой."
    ),
}

FAQ_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("растамож", "тамож", "пошлин"), "customs_kr_ru"),
    (("доставк", "морем"), "delivery_kr_vladivostok"),
    (("риск", "надёжн"), "risks_auction"),
]

QUESTION_MARKERS = ("?", "сколько", "как ", "какая", "какие", "что ", "почему", "когда", "расскажи")


def match_faq_topic(text: str) -> str | None:
    """FAQ отвечает только на «вопросные» сообщения — не мешает сбору критериев."""
    low = text.lower()
    if not any(marker in low for marker in QUESTION_MARKERS):
        return None
    for keywords, topic in FAQ_KEYWORDS:
        if any(kw in low for kw in keywords):
            return topic
    return None


async def answer_faq(redis: Redis, topic: str) -> str | None:
    cached = await redis.get(f"faq:{topic}")
    if cached:
        return cached.decode() if isinstance(cached, bytes) else cached
    answer = FAQ.get(topic)
    if answer:
        await redis.setex(f"faq:{topic}", get_settings().faq_cache_ttl_sec, answer)
    return answer


# --- хранилище DialogState (ТЗ п. 7.6) -----------------------------------


def new_state(user_id: int) -> DialogState:
    from app.schemas.dialog import Message

    now = datetime.now(UTC)
    return DialogState(
        session_id=str(uuid.uuid4()),
        user_id=user_id,
        created_at=now,
        updated_at=now,
        messages=[Message(role="assistant", content="Опишите, какую машину ищете.")],
    )


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
        state.updated_at = datetime.now(UTC)
        await self._redis.setex(
            dialog_key(state.user_id), self._ttl, state.model_dump_json()
        )

    async def delete(self, user_id: int) -> None:
        await self._redis.delete(dialog_key(user_id))


# --- архивация и финализация (ТЗ п. 7.6) ---------------------------------


async def archive_session(session: AsyncSession, state: DialogState) -> None:
    """Сохранить/обновить сессию в Postgres — для аналитики и дообучения."""
    existing = await session.get(DialogSession, uuid.UUID(state.session_id))
    if existing is None:
        existing = DialogSession(id=uuid.UUID(state.session_id))
        session.add(existing)
    existing.user_id = state.user_id
    existing.status = state.status
    existing.criteria = state.criteria.model_dump(mode="json")
    existing.turn_count = state.turn_count
    existing.messages = [m.model_dump(mode="json") for m in state.messages]
    if existing.created_at is None:
        existing.created_at = state.created_at
    if state.status in ("done", "searching"):
        existing.finished_at = datetime.now(UTC)


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
