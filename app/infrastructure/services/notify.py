"""Логика уведомлений (этап 4, ТЗ п. 4.6 Send Worker, 8.6).

Здесь нет обращений к Telegram — только матчинг фильтров, антидубли
(sent_log + Redis), форматирование карточек и digest-буфер.
"""

import logging
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.db.models import Listing, SentLog, User, UserFilter
from app.domain.search.criteria import SearchCriteria
from app.infrastructure.services.search import build_listing_query

from app.domain.notifications.rules import (
    digest_key,
    format_listing_card,  # noqa: F401 — ре-экспорт для transport/workers
    in_quiet_hours as _in_quiet_hours,
    matches_criteria,
    notified_key,
    parse_quiet_hours,  # noqa: F401 — ре-экспорт для transport/workers
    _fmt_price,  # noqa: F401 — ре-экспорт для workers.send
)

logger = logging.getLogger(__name__)


def in_quiet_hours(quiet_hours: str | None, now: datetime | None = None) -> bool:
    """Обёртка: подставляет дефолт из настроек (домен настроек не знает)."""
    return _in_quiet_hours(
        quiet_hours, now, get_settings().notify_quiet_hours_default
    )



# ---------------------------------------------------------------------------
# Антидубли (ТЗ п. 8.6): sent_log + notified:{user_id}:{listing_id} TTL 7 дней




async def was_sent(
    redis: Redis, user_id: int, listing_id: int, kind: str = "new"
) -> bool:
    return bool(await redis.get(notified_key(user_id, listing_id, kind)))


async def mark_sent(
    session: AsyncSession,
    redis: Redis,
    user_id: int,
    listing_id: int,
    kind: str = "new",
) -> None:
    """Фиксация отправки: строка в sent_log (идемпотентно) + Redis-ключ с TTL 7д.

    kind отличает повторные отправки того же объявления (price_drop и т.д.):
    PK sent_log = (user_id, listing_id, kind), миграция 0010.
    """
    await session.execute(
        pg_insert(SentLog)
        .values(user_id=user_id, listing_id=listing_id, kind=kind)
        .on_conflict_do_nothing()
    )
    await session.commit()
    await redis.setex(
        notified_key(user_id, listing_id, kind),
        get_settings().notify_sent_ttl_sec,
        "1",
    )


async def mark_sent_many(
    session: AsyncSession,
    redis: Redis,
    user_id: int,
    listing_ids: list[int],
    kind: str = "new",
) -> None:
    """Пакетная `mark_sent` (TECHDEBT 9.2): один bulk insert + pipeline SETEX.

    Дайджест на 50 пользователей x 5 карточек — это 250 отдельных
    insert+commit+SETEX в наивном варианте; здесь — 1 запрос + 1 pipeline.
    """
    if not listing_ids:
        return
    await session.execute(
        pg_insert(SentLog)
        .values([
            {"user_id": user_id, "listing_id": lid, "kind": kind}
            for lid in listing_ids
        ])
        .on_conflict_do_nothing()
    )
    await session.commit()
    ttl = get_settings().notify_sent_ttl_sec
    pipe = redis.pipeline(transaction=False)
    for lid in listing_ids:
        pipe.setex(notified_key(user_id, lid, kind), ttl, "1")
    await pipe.execute()


async def event_audience(session: AsyncSession, listing_id: int) -> list[int]:
    """Аудитория статусных уведомлений (PLAN.md, фаза 3): пользователи, уже
    получавшие карточку этого объявления (sent_log)."""
    result = await session.execute(
        select(SentLog.user_id).where(SentLog.listing_id == listing_id).distinct()
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Тихие часы ("23:00-08:00"; интерпретируем в UTC — упрощение этапа 4)






# ---------------------------------------------------------------------------
# Матчинг объявления под критерии фильтра (Python-side, согласовано с search.py)






# ---------------------------------------------------------------------------
# Сбор свежих объявлений по активным фильтрам


async def _filter_rows(session: AsyncSession, mode: str) -> list[UserFilter]:
    result = await session.execute(
        select(UserFilter).where(UserFilter.notify_mode == mode)
    )
    return list(result.scalars().all())


async def collect_for_filters(
    session: AsyncSession,
    redis: Redis,
    mode: str,
    window_sec: int | None = None,
) -> list[tuple[UserFilter, list[Listing]]]:
    """Свежие неотправленные объявления для всех фильтров в режиме `mode`.

    window_sec ограничивает «свежесть» по parsed_at (обычно = интервалу скана).
    """
    settings = get_settings()
    window = window_sec if window_sec is not None else settings.notify_scan_window_sec
    since = datetime.now(timezone.utc) - timedelta(seconds=window)

    pairs: list[tuple[UserFilter, list[Listing]]] = []
    for flt in await _filter_rows(session, mode):
        user = await session.get(User, flt.user_id)
        if user is not None and not user.is_active:
            continue

        criteria = SearchCriteria(**(flt.criteria or {}))
        stmt = build_listing_query(criteria).where(Listing.parsed_at >= since).limit(20)
        listings = list((await session.execute(stmt)).scalars().all())

        fresh = [
            item
            for item in listings
            if matches_criteria(item, criteria)
            and not await was_sent(redis, flt.user_id, item.id)
        ]
        if fresh:
            pairs.append((flt, fresh))
    return pairs


# ---------------------------------------------------------------------------
# Digest-буфер в Redis (список listing_id на пользователя)




async def buffer_for_digest(redis: Redis, user_id: int, listing_id: int) -> None:
    await redis.rpush(digest_key(user_id), str(listing_id))


async def read_digest(redis: Redis, user_id: int, max_cards: int | None = None) -> list[int]:
    raw = await redis.lrange(digest_key(user_id), 0, -1)
    ids: list[int] = []
    for item in raw:
        value = item.decode() if isinstance(item, bytes) else str(item)
        try:
            ids.append(int(value))
        except ValueError:
            continue
    limit = max_cards if max_cards is not None else get_settings().digest_max_cards
    return ids[-limit:]  # последние N (наиболее свежие)


async def clear_digest(redis: Redis, user_id: int) -> None:
    await redis.delete(digest_key(user_id))


# ---------------------------------------------------------------------------
# Карточка объявления (ТЗ п. 8.5)




