"""Логика уведомлений (этап 4, ТЗ п. 4.6 Send Worker, 8.6).

Здесь нет обращений к Telegram — только матчинг фильтров, антидубли
(sent_log + Redis), форматирование карточек и digest-буфер.
"""

import html
import logging
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Listing, SentLog, User, UserFilter
from app.schemas.criteria import SearchCriteria
from app.services.search import build_listing_query

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Антидубли (ТЗ п. 8.6): sent_log + notified:{user_id}:{listing_id} TTL 7 дней


def notified_key(user_id: int, listing_id: int) -> str:
    return f"notified:{user_id}:{listing_id}"


async def was_sent(redis: Redis, user_id: int, listing_id: int) -> bool:
    return bool(await redis.get(notified_key(user_id, listing_id)))


async def mark_sent(
    session: AsyncSession, redis: Redis, user_id: int, listing_id: int
) -> None:
    """Фиксация отправки: строка в sent_log (идемпотентно) + Redis-ключ с TTL 7д."""
    await session.execute(
        pg_insert(SentLog)
        .values(user_id=user_id, listing_id=listing_id)
        .on_conflict_do_nothing()
    )
    await session.commit()
    await redis.setex(
        notified_key(user_id, listing_id),
        get_settings().notify_sent_ttl_sec,
        "1",
    )


# ---------------------------------------------------------------------------
# Тихие часы ("23:00-08:00"; интерпретируем в UTC — упрощение этапа 4)


def parse_quiet_hours(quiet_hours: str | None) -> tuple[time, time] | None:
    if not quiet_hours or "-" not in quiet_hours:
        return None
    try:
        start_s, end_s = quiet_hours.split("-", 1)
        start = time.fromisoformat(start_s.strip())
        end = time.fromisoformat(end_s.strip())
    except ValueError:
        logger.warning("bad_quiet_hours: %r", quiet_hours)
        return None
    return start, end


def in_quiet_hours(quiet_hours: str | None, now: datetime | None = None) -> bool:
    parsed = parse_quiet_hours(quiet_hours or get_settings().notify_quiet_hours_default)
    if parsed is None:
        return False
    start, end = parsed
    current = (now or datetime.now(timezone.utc)).time().replace(tzinfo=None)
    if start <= end:
        return start <= current < end
    # ночной интервал через полночь (23:00-08:00)
    return current >= start or current < end


# ---------------------------------------------------------------------------
# Матчинг объявления под критерии фильтра (Python-side, согласовано с search.py)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def matches_criteria(listing: Any, criteria: SearchCriteria) -> bool:
    """Проверка одного объявления по SearchCriteria (NULL в фильтре = не важно)."""
    if criteria.make and criteria.make.lower() not in _norm(listing.make):
        return False
    if criteria.model and criteria.model.lower() not in _norm(listing.model):
        return False
    if criteria.year_from and (listing.year or 0) < criteria.year_from:
        return False
    if criteria.year_to and listing.year and listing.year > criteria.year_to:
        return False
    if criteria.price_min and (listing.price or 0) < criteria.price_min:
        return False
    if criteria.price_max and listing.price and listing.price > criteria.price_max:
        return False
    if criteria.mileage_max and (listing.mileage_km or 0) > criteria.mileage_max:
        return False
    if criteria.fuel and criteria.fuel.lower() != _norm(getattr(listing, "fuel", None)):
        return False
    if criteria.sources and listing.source not in criteria.sources:
        return False
    return True


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


def digest_key(user_id: int) -> str:
    return f"digest:{user_id}"


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


def _fmt_price(price: Any, currency: str | None) -> str:
    if price is None:
        return "цена по запросу"
    try:
        amount = f"{Decimal(str(price)):,.0f}".replace(",", " ")
    except Exception:
        amount = str(price)
    cur = {"USD": "$", "KRW": "₩", "EUR": "€", "CNY": "¥", "JPY": "¥"}.get(
        (currency or "USD").upper(), currency or "$"
    )
    return f"{amount} {cur}"


def format_listing_card(listing: Any) -> str:
    """HTML-карточка объявления (фото/кнопки добавляет отправитель)."""
    title = " ".join(str(part) for part in (listing.make, listing.model, listing.year) if part)
    lines = [f"🚗 <b>{html.escape(title)}</b>" if title else "🚗 <b>Объявление</b>"]
    lines.append(f"💰 {_fmt_price(listing.price, listing.currency)}")
    if listing.mileage_km:
        lines.append(f"📏 {listing.mileage_km:,} км".replace(",", " "))
    lines.append(f"🔗 {html.escape(str(listing.source).capitalize())}")
    lines.append(html.escape(str(listing.url)))
    return "\n".join(lines)