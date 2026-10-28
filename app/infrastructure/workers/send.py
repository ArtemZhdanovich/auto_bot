"""Send Worker — этап 4 (ТЗ п. 4.6, 8.6). Очередь: RabbitMQ (`send`).

Запуск: python -m app.infrastructure.workers.send

- instant: notify_new_listings (после парсинга) → фанаут задач
  send_listing_notification (rate limit 30 сек/пользователь, тихие часы);
- digest: cron 09:00 и 20:00 UTC, 3–5 карточек одним сообщением;
- подписки: cron 12:00 UTC — напоминания за 3/1 день, даунгрейд;
- проданные избранного: cron 04:30 UTC — сообщение «продана, ищем похожие?»
  и удаление из избранного в любом случае (успех/ошибка отправки);
- антидубли: sent_log + notified:{uid}:{lid} (TTL 7 дней) — в Redis;
- Telegram Bot API напрямую через httpx, обработка 429 (retry_after).
"""

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from redis.asyncio import Redis

from app.config import get_settings
from app.application.market.dto import (
    LISTING_STATUS_ACCEPTED,
    LISTING_STATUS_EXPIRED,
)
from app.domain.subscription.policies import effective_tier
from app.infrastructure.db.models import Listing, ListingPriceHistory, User, UserFilter
from app.infrastructure.db.session import get_session, session_factory
from app.infrastructure.messaging.legacy_worker import QUEUE_SEND, CronSpec, TaskPublisher, TaskWorker
from app.infrastructure.services import rate_limit
from app.infrastructure.services.centrifugo import CentrifugoPublisher
from app.infrastructure.services.dialog import cleanup_expired_dialogs
from app.infrastructure.services.notify import (
    SOLD_FAV_KIND,
    _fmt_price,
    buffer_for_digest,
    clear_digest,
    collect_for_filters,
    collect_sold_favorites,
    drop_favorites,
    event_audience,
    format_listing_card,
    in_quiet_hours,
    mark_sent,
    mark_sent_many,
    read_digest,
    was_sent,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)

# TECHDEBT 9.7: общий клиент Telegram API (ленивый, один на процесс).
_TG_CLIENT: httpx.AsyncClient | None = None


def _tg_client() -> httpx.AsyncClient:
    global _TG_CLIENT
    if _TG_CLIENT is None or _TG_CLIENT.is_closed:
        _TG_CLIENT = httpx.AsyncClient(timeout=15.0)
    return _TG_CLIENT


async def close_telegram_client() -> None:
    """Закрыть общий клиент Telegram (shutdown воркера)."""
    global _TG_CLIENT
    if _TG_CLIENT is not None and not _TG_CLIENT.is_closed:
        await _TG_CLIENT.aclose()
    _TG_CLIENT = None

TELEGRAM_API = "https://api.telegram.org"


async def send_telegram_message(
    bot_token: str,
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """sendMessage с parse_mode=HTML. 429 → ждём retry_after и повторяем раз.

    `reply_markup` — опциональная инлайн-клавиатура сырым словарём Bot API
    (воркер ходит в Telegram httpx'ом, без aiogram-типов); существующие
    вызовы без кнопок остаются без изменений (дефолт None).

    Перед отправкой — Token Bucket (ТЗ п. 12.6): ~30 msg/s глобально,
    ~1 msg/s на чат; Redis лежит → отправка не блокируется (fail-open).
    """
    if not bot_token:
        logger.warning("telegram_send_skipped: no BOT_TOKEN")
        return False, "no_bot_token"

    waited = await rate_limit.rate_limit_wait(chat_id)
    if waited:
        logger.info("telegram_rate_limited_local: waited=%.2fs chat=%s", waited, chat_id)

    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    for attempt in (1, 2):
        try:
            # TECHDEBT 9.7: один httpx.AsyncClient на процесс (пул соединений),
            # закрывается в `shutdown` через `close_telegram_client()`.
            resp = await _tg_client().post(url, json=payload)
        except httpx.HTTPError as exc:
            logger.error("telegram_send_failed: %s", exc)
            return False, str(exc)

        if resp.status_code == 429 and attempt == 1:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 1)
            logger.warning("telegram_rate_limited: retry_after=%s", retry_after)
            await asyncio.sleep(min(float(retry_after), 30.0))
            continue
        if resp.status_code == 200:
            return True, None
        logger.error(
            "telegram_send_bad_status: %s %s", resp.status_code, resp.text[:200]
        )
        return False, f"status_{resp.status_code}"
    return False, "rate_limited"


async def _user_quiet_hours(session: Any, user_id: int) -> str | None:
    result = await session.execute(
        select(UserFilter.quiet_hours).where(UserFilter.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none()


async def send_listing_notification(
    ctx: dict[str, Any], user_id: int, listing_id: int
) -> dict[str, Any]:
    """Instant-уведомление одному пользователю об одном объявлении."""
    settings = get_settings()
    redis: Redis = ctx["redis"]
    bot_token: str = ctx.get("bot_token", "")

    # антидубль (п. 8.6)
    if await was_sent(redis, user_id, listing_id):
        return {"user_id": user_id, "listing_id": listing_id, "skipped": "already_sent"}

    # rate limit: не чаще 1 сообщения в 30 сек на пользователя
    allowed = await redis.set(
        f"rl:notify:{user_id}", "1", nx=True, ex=settings.notify_rate_limit_sec
    )
    if not allowed:
        return {"user_id": user_id, "listing_id": listing_id, "skipped": "rate_limited"}

    async with session_factory() as session:
        listing = await session.get(Listing, listing_id)
        if listing is None:
            return {"user_id": user_id, "listing_id": listing_id, "error": "not_found"}

        if in_quiet_hours(await _user_quiet_hours(session, user_id)):
            # тихие часы: в буфер дайджеста, чтобы не потерять
            await buffer_for_digest(redis, user_id, listing_id)
            return {"user_id": user_id, "listing_id": listing_id, "skipped": "quiet_hours"}

        ok, err = await send_telegram_message(
            bot_token, user_id, format_listing_card(listing)
        )
        if not ok:
            return {"user_id": user_id, "listing_id": listing_id, "error": err}

        await mark_sent(session, redis, user_id, listing_id)
        return {"user_id": user_id, "listing_id": listing_id, "sent": True}


def format_status_card(listing: Any, kind: str, old_price: Any = None) -> str:
    """Карточка статусного события (PLAN.md, фаза 3): price_drop/gone/returned."""
    if kind == "price_drop":
        line = "⬇️ <b>Цена снизилась</b>"
        if old_price is not None:
            line += (
                f": {_fmt_price(old_price, listing.currency)} → "
                f"{_fmt_price(listing.price, listing.currency)}"
            )
    elif kind == "returned":
        line = "✅ <b>Снова в продаже</b>"
    else:  # gone
        line = "🔴 <b>Объявление исчезло с источника</b>"
    return line + "\n" + format_listing_card(listing)


async def _old_price(session: Any, listing_id: int) -> Any:
    """Предыдущая точка цены (вторая с конца) — для «было → стало»."""
    result = await session.execute(
        select(ListingPriceHistory.price)
        .where(ListingPriceHistory.listing_id == listing_id)
        .order_by(ListingPriceHistory.recorded_at.desc())
        .limit(2)
    )
    rows = list(result.scalars().all())
    return rows[1] if len(rows) > 1 else None


async def send_listing_status(
    ctx: dict[str, Any], user_id: int, listing_id: int, kind: str
) -> dict[str, Any]:
    """Статусное уведомление (price_drop/gone/returned) — тот же контур, что
    instant: rate limit, тихие часы (в буфер дайджеста), антидубль по kind."""
    settings = get_settings()
    redis: Redis = ctx["redis"]
    bot_token: str = ctx.get("bot_token", "")

    if await was_sent(redis, user_id, listing_id, kind):
        return {
            "user_id": user_id,
            "listing_id": listing_id,
            "kind": kind,
            "skipped": "already_sent",
        }
    allowed = await redis.set(
        f"rl:notify:{user_id}", "1", nx=True, ex=settings.notify_rate_limit_sec
    )
    if not allowed:
        return {
            "user_id": user_id,
            "listing_id": listing_id,
            "kind": kind,
            "skipped": "rate_limited",
        }

    async with session_factory() as session:
        listing = await session.get(Listing, listing_id)
        if listing is None:
            return {
                "user_id": user_id,
                "listing_id": listing_id,
                "kind": kind,
                "error": "not_found",
            }

        if in_quiet_hours(await _user_quiet_hours(session, user_id)):
            # тихие часы: в буфер дайджеста, чтобы не потерять
            await buffer_for_digest(redis, user_id, listing_id)
            return {
                "user_id": user_id,
                "listing_id": listing_id,
                "kind": kind,
                "skipped": "quiet_hours",
            }

        old_price = (
            await _old_price(session, listing_id) if kind == "price_drop" else None
        )
        ok, err = await send_telegram_message(
            bot_token, user_id, format_status_card(listing, kind, old_price)
        )
        if not ok:
            return {
                "user_id": user_id,
                "listing_id": listing_id,
                "kind": kind,
                "error": err,
            }

        await mark_sent(session, redis, user_id, listing_id, kind=kind)
        return {"user_id": user_id, "listing_id": listing_id, "kind": kind, "sent": True}


async def listing_status_scan(
    ctx: dict[str, Any], kind: str, listing_ids: list[int]
) -> dict[str, Any]:
    """Фанаут статусных уведомлений аудитории sent_log (кто уже видел карточку).

    Вызывается fetch-воркером: price_drop/returned сразу после парсинга,
    gone — из stale_check_job (cron 04:00 UTC).
    """
    publisher: TaskPublisher | None = ctx.get("publisher")
    dispatched = 0
    async with session_factory() as session:
        for listing_id in listing_ids:
            for user_id in await event_audience(session, listing_id):
                if publisher is not None:
                    await publisher.enqueue(
                        "send_listing_status", user_id, listing_id, kind
                    )
                dispatched += 1

    if dispatched:
        logger.info("status_scan_done: kind=%s dispatched=%s", kind, dispatched)
    return {"kind": kind, "dispatched": dispatched}


async def notify_new_listings(
    ctx: dict[str, Any], source: str | None = None
) -> dict[str, Any]:
    """Скан фильтров после парсинга: instant → задачи отправки, digest → буфер."""
    redis: Redis = ctx["redis"]
    publisher: TaskPublisher | None = ctx.get("publisher")
    counts = {"instant": 0, "digest": 0}

    async with session_factory() as session:
        for mode in ("instant", "digest"):
            for flt, listings in await collect_for_filters(session, redis, mode):
                for item in listings:
                    if mode == "instant":
                        if publisher is not None:
                            await publisher.enqueue(
                                "send_listing_notification", flt.user_id, item.id
                            )
                        counts["instant"] += 1
                    else:
                        await buffer_for_digest(redis, flt.user_id, item.id)
                        counts["digest"] += 1

    if any(counts.values()):
        logger.info("notify_scan_done: %s source=%s", counts, source)
    return counts


def _digest_due(tier: str, slot: str, now=None) -> bool:
    """Каденс дайджеста по тарифу (экономика 2026-10).

    - free: раз в НЕДЕЛЮ — утренний слот понедельника;
    - pro: каждый день (утро) + вечерний слот;
    - business: буфер обычно пуст (instant при публикации), но досылаем
      утром и вечером на случай тихих часов.
    """
    from datetime import datetime as _dt

    current = now or _dt.now(timezone.utc)
    if slot == "evening":
        return tier in ("pro", "business")
    return tier != "free" or current.weekday() == 0  # Monday == 0


async def _digest_run(ctx: dict[str, Any], slot: str) -> dict[str, Any]:
    """Прогон дайджеста: карточки одним сообщением по каденсу тарифа."""
    redis: Redis = ctx["redis"]
    bot_token: str = ctx.get("bot_token", "")
    sent = skipped = 0

    # TECHDEBT 9.1: SCAN вместо KEYS — не блокирует event loop/Redis
    # на всём keyspace (ключей может быть тысячи).
    async for key in redis.scan_iter("digest:*"):
        raw_key = key.decode() if isinstance(key, bytes) else str(key)
        try:
            user_id = int(raw_key.split(":", 1)[1])
        except (IndexError, ValueError):
            continue

        ids = await read_digest(redis, user_id)
        if not ids:
            await clear_digest(redis, user_id)
            continue

        async with session_factory() as session:
            user = await session.get(User, user_id)
            if user is None or not user.is_active:
                await clear_digest(redis, user_id)
                continue
            if not _digest_due(effective_tier(user), slot):
                continue  # free ждёт понедельника — буфер не трогаем

            cards: list[str] = []
            for listing_id in ids:
                listing = await session.get(Listing, listing_id)
                if listing is None:
                    continue
                cards.append(format_listing_card(listing))

            if cards:
                header = f"\U0001F4EC <b>Дайджест:</b> {len(cards)} новых вариантов\n\n"
                ok, _err = await send_telegram_message(
                    bot_token, user_id, header + "\n\n".join(cards)
                )
            else:
                ok = True  # буфер пуст по факту — просто чистим

            if ok:
                # TECHDEBT 9.2: одна batch-запись (bulk insert + pipeline)
                # вместо N отдельных insert+commit+SETEX.
                await mark_sent_many(session, redis, user_id, ids)
                sent += len(ids)
            else:
                skipped += len(ids)  # оставляем буфер до следующего прогона

        if ok:
            await clear_digest(redis, user_id)

    logger.info("digest_sent: slot=%s sent=%s kept=%s", slot, sent, skipped)
    return {"sent": sent, "kept": skipped}


async def digest_job_morning(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron 09:00 UTC: всем тарифам (free — только по понедельникам)."""
    return await _digest_run(ctx, "morning")


async def digest_job_evening(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron 20:00 UTC: Pro/Business (вечерняя подборка)."""
    return await _digest_run(ctx, "evening")


async def subscription_check_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron ежедневно (12:00 UTC): напоминания за 3/1 день и даунгрейд (ТЗ п. 8.7).

    - за 3 дня и за 1 день до окончания — уведомление;
    - после окончания (не позднее 3 дней) — возврат на Free + сообщение.

    Бессрочный Business (subscription_until IS NULL, выдан партнёрским
    контуром) в выборку не попадает и не даунгрейдится.
    """
    redis: Redis = ctx["redis"]
    bot_token: str = ctx.get("bot_token", "")
    now = datetime.now(timezone.utc)
    reminded = downgraded = 0

    async with session_factory() as session:
        result = await session.execute(
            select(User).where(
                User.subscription_tier != "free",
                User.subscription_until.is_not(None),
                # TECHDEBT 9.8: деактивированным не пишем — ни напоминаний,
                # ни даунгрейда (мусорные сообщения на мёртвый chat_id).
                User.is_active.is_(True),
            )
        )
        users = list(result.scalars().all())
        for user in users:
            until = user.subscription_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            days_left = (until - now).days

            if days_left <= 0:
                if (now - until).total_seconds() > 3 * 86400:
                    continue  # давно истёкших не спамим повторно
                user.subscription_tier = "free"
                # TECHDEBT 9.3: until очищаем — иначе «истёкший» пользователь
                # вечно попадает в выборку и в карточках висит старая дата.
                user.subscription_until = None
                await session.commit()
                await redis.delete(f"sub:reminded:{user.telegram_id}")
                await send_telegram_message(
                    bot_token,
                    user.telegram_id,
                    "⌛ Подписка закончилась — вы переведены на тариф Free.\n"
                    "Продлить: /subscribe (Pro даёт instant-уведомления и 20 фильтров).",
                )
                downgraded += 1
            elif days_left in (1, 3):
                already = await redis.get(f"sub:reminded:{user.telegram_id}:{days_left}")
                if already:
                    continue
                await redis.set(
                    f"sub:reminded:{user.telegram_id}:{days_left}",
                    "1",
                    ex=2 * 86400,
                )
                await send_telegram_message(
                    bot_token,
                    user.telegram_id,
                    f"🔔 Подписка <b>{user.subscription_tier}</b> закончится через "
                    f"{days_left} дн. Продлить со скидкой: /subscribe",
                )
                reminded += 1

    if reminded or downgraded:
        logger.info(
            "subscription_check_done: reminded=%s downgraded=%s", reminded, downgraded
        )
    return {"reminded": reminded, "downgraded": downgraded}


async def dialog_cleanup_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron ежедневно: снос архивных диалогов, молчащих сутки с последнего
    сообщения (п. «диалоги сносить через сутки»)."""
    async for session in get_session():
        deleted = await cleanup_expired_dialogs(session)
    return {"dialog_sessions_deleted": deleted}


# --- проданные избранного (требование владельца) --------------------------------


SOLD_FAV_MAX_PER_MESSAGE = 5  # до 5 машин одним сообщением одному пользователю


def _sold_fav_title(listing: Any) -> str:
    """Заголовок для списка проданных: переведённый тайтл или make model year."""
    title = getattr(listing, "title_ru", None) or " ".join(
        str(part) for part in (listing.make, listing.model, listing.year) if part
    )
    return title or "Объявление"


def format_sold_favorites_card(items: list[Any]) -> str:
    """Сообщение «машина из избранного продана» (до 5 машин одним списком)."""
    titles = [html.escape(_sold_fav_title(item)) for item in items]
    if len(titles) == 1:
        return f"🚗 <b>{titles[0]}</b> продана — убрали из избранного. Ищем похожие?"
    lines = ["🚗 <b>Машины из избранного проданы</b> — убрали их:"]
    lines.extend(f"• <b>{title}</b>" for title in titles)
    lines.append("Ищем похожие?")
    return "\n".join(lines)


def _sold_fav_reply_markup(
    items: list[Any], miniapp_url: str
) -> dict[str, Any] | None:
    """Кнопки «Ищем похожие» → мини-апп `/analyses?similar={listing_id}`.

    Markup — сырой словарь Bot API (воркер шлёт httpx, без aiogram-типов);
    web_app-кнопка требует https (как `_miniapp_button` в боте), http-адрес
    открываем обычной url-кнопкой. Пустой miniapp_url — без кнопки, только текст.
    """
    base = (miniapp_url or "").strip()
    if not base:
        return None
    rows: list[list[dict[str, Any]]] = []
    for listing in items:
        url = f"{base.rstrip('/')}/analyses?similar={listing.id}"
        label = html.escape(_sold_fav_title(listing)[:32])
        text = "🔎 Ищем похожие" if len(items) == 1 else f"🔎 Похожие: {label}"
        button = (
            {"text": text, "web_app": {"url": url}}
            if url.startswith("https://")
            else {"text": text, "url": url}
        )
        rows.append([button])
    return {"inline_keyboard": rows}


async def favorites_sold_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron 04:30 UTC (после stale_check_job 04:00 в fetch-воркере): машины
    из избранного проданы → сообщение и удаление из избранного.

    Одному пользователю с несколькими проданными — одно сообщение со
    списком до SOLD_FAV_MAX_PER_MESSAGE машин; хвост — следующим прогоном.
    Favorite удаляем в любом случае (успех/ошибка отправки) — требование
    владельца. Антидубль — kind='sold_fav' (sent_log + Redis TTL).
    """
    settings = get_settings()
    redis: Redis = ctx["redis"]
    bot_token: str = ctx.get("bot_token", "")
    removed = notified = send_failed = 0

    async with session_factory() as session:
        by_user: dict[int, list[Listing]] = {}
        for user_id, _listing_id, listing in await collect_sold_favorites(
            session, redis
        ):
            by_user.setdefault(user_id, []).append(listing)

        for user_id, listings in by_user.items():
            batch = listings[:SOLD_FAV_MAX_PER_MESSAGE]
            ids = [item.id for item in batch]
            ok, _err = await send_telegram_message(
                bot_token,
                user_id,
                format_sold_favorites_card(batch),
                reply_markup=_sold_fav_reply_markup(batch, settings.miniapp_url),
            )
            # из избранного убираем в любом случае — даже если отправка не прошла
            await drop_favorites(session, user_id, ids)
            if ok:
                await mark_sent_many(session, redis, user_id, ids, kind=SOLD_FAV_KIND)
                notified += len(ids)
            else:
                send_failed += len(ids)
            removed += len(ids)

    if notified or send_failed:
        logger.info(
            "favorites_sold_done: removed=%s notified=%s send_failed=%s",
            removed,
            notified,
            send_failed,
        )
    return {"removed": removed, "notified": notified, "send_failed": send_failed}


async def market_placement_expiry(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron 03:40 UTC: экспирация размещений user-лотов рынка (06 §2-§3, 10 §1).

    - включённые лоты платных тарифов не трогаем (`effective_tier != free`);
    - активный лот без placement-дат у free-владельца — даунгрейд
      включённого лота после ухода с тарифа: `free_until = now+7d`
      (однократно: дальше у лота есть дата и работает экспирация);
    - `max(free, paid) < now` у одобренного лота — `status='expired'`
      (is_active НЕ трогаем — 10 §1: статус жизни в `status`, is_active
      остаётся для совместимости старых фильтров) и событие
      `listing_status` в личный канал centrifugo `user:{owner_user_id}`.
    Needs_payment-лоты (неактивные без дат) не выбираются — им нечего
    экспирировать; expired-лоты повторно не матчатся (фильтр по статусу).
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    publisher: CentrifugoPublisher | None = ctx.get("centrifugo")
    downgraded = expired = 0
    expired_lots: list[tuple[int, int]] = []  # (owner_user_id, listing_id)

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Listing).where(
                        Listing.owner_type == "user",
                        Listing.owner_user_id.is_not(None),
                        Listing.is_active.is_(True),
                        Listing.status == LISTING_STATUS_ACCEPTED,
                    )
                )
            )
            .scalars()
            .all()
        )
        owners = {
            row.owner_user_id
            for row in rows
            if row.owner_user_id is not None
        }
        users: dict[int, Any] = {}
        if owners:
            users = {
                u.telegram_id: u
                for u in (
                    (
                        await session.execute(
                            select(User).where(User.telegram_id.in_(owners))
                        )
                    )
                    .scalars()
                    .all()
                )
            }

        for row in rows:
            owner = users.get(row.owner_user_id)
            if owner is not None and effective_tier(owner) != "free":
                continue  # включённый лот платного тарифа (§3)
            moments = [
                value.replace(tzinfo=timezone.utc)
                if value.tzinfo is None
                else value
                for value in (row.placement_free_until, row.placement_paid_until)
                if value is not None
            ]
            if not moments:
                # Included-даунгрейд (§3): одна бесплатная неделя с момента,
                # когда экспиратор впервые увидел лот у free-владельца.
                row.placement_free_until = now + timedelta(
                    days=settings.market_free_days
                )
                downgraded += 1
                continue
            if max(moments) < now:
                # 10 §1: экспиратор пишет status вместо is_active=false.
                row.status = LISTING_STATUS_EXPIRED
                expired += 1
                if row.owner_user_id is not None:
                    expired_lots.append((row.owner_user_id, row.id))
        await session.commit()

    # Публикуем после commit: событие только про устоявшееся состояние.
    if publisher is not None:
        for owner_user_id, listing_id in expired_lots:
            await publisher.publish(
                f"user:{owner_user_id}",
                {
                    "type": "listing_status",
                    "listing_id": listing_id,
                    "status": "expired",
                },
            )
    if downgraded or expired:
        logger.info(
            "market_placement_expiry: downgraded=%s expired=%s",
            downgraded,
            expired,
        )
    return {"downgraded": downgraded, "expired": expired}


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    # Redis (данные: дедуп, rate limit, digest-буферы, ключи подписок)
    redis = Redis.from_url(settings.redis_url)
    ctx["redis"] = redis
    # Token Bucket Telegram (ТЗ п. 12.6): 30 msg/s глобально, 1 msg/s на чат
    rate_limit.init_rate_limiter(
        redis, settings.telegram_rate_global_per_sec, settings.telegram_rate_chat_per_sec
    )
    ctx["bot_token"] = settings.bot_token.get_secret_value()
    ctx["publisher"] = TaskPublisher(settings.rabbitmq_url)
    # Публикация lot_expired в личные каналы centrifugo (экспиратор рынка).
    ctx["centrifugo"] = CentrifugoPublisher(
        settings.centrifugo_url,
        settings.centrifugo_http_api_key.get_secret_value(),
        settings.centrifugo_secret.get_secret_value(),
    )
    logger.info("send_worker_started")


async def shutdown(ctx: dict[str, Any]) -> None:
    publisher: TaskPublisher | None = ctx.get("publisher")
    if publisher:
        await publisher.aclose()
    rate_limit.reset_rate_limiter()
    await close_telegram_client()
    redis: Redis | None = ctx.get("redis")
    if redis:
        await redis.aclose()
    logger.info("send_worker_stopped")


HANDLERS = {
    "send_listing_notification": send_listing_notification,
    "send_listing_status": send_listing_status,
    "notify_new_listings": notify_new_listings,
    "listing_status_scan": listing_status_scan,
    "digest_job": digest_job_morning,  # имя задачи очереди сохранено
    "digest_job_morning": digest_job_morning,
    "digest_job_evening": digest_job_evening,
    "subscription_check_job": subscription_check_job,
    "favorites_sold_job": favorites_sold_job,
    "market_placement_expiry": market_placement_expiry,
}

# Дайджест 09:00 и 20:00 UTC, подписки — 12:00 UTC (ТЗ п. 8.6, 8.7),
# чистка диалогов старше суток — 04:00 UTC; проданные избранные — 04:30 UTC,
# после stale_check_job (fetch-воркер, 04:00): Listing.is_active успевает
# обновиться до выборки «проданных». Экспирация размещений рынка — 03:40 UTC
# (06 §2): до статуса-скана и утреннего дайджеста.
CRON_JOBS = [
    CronSpec(digest_job_morning, hour=9, minute=0),
    CronSpec(digest_job_evening, hour=20, minute=0),
    CronSpec(subscription_check_job, hour=12, minute=0),
    CronSpec(dialog_cleanup_job, hour=4, minute=0),
    CronSpec(favorites_sold_job, hour=4, minute=30),
    CronSpec(market_placement_expiry, hour=3, minute=40),
]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = get_settings()
    ctx: dict[str, Any] = {}
    await startup(ctx)
    # Heartbeat для healthcheck (консюмер без HTTP-порта)
    from app.infrastructure.runtime.heartbeat import start_heartbeat

    await start_heartbeat("send")
    worker = TaskWorker(
        amqp_url=settings.rabbitmq_url,
        bindings={QUEUE_SEND: list(HANDLERS)},
        handlers=HANDLERS,
        ctx=ctx,
        concurrency=5,   # как arq max_jobs=5
        job_timeout=60,  # как arq job_timeout
        cron_jobs=CRON_JOBS,
    )
    try:
        await worker.run()
    finally:
        await shutdown(ctx)


if __name__ == "__main__":
    asyncio.run(main())
