"""Send Worker — этап 4 (ТЗ п. 4.6, 8.6). Очередь: RabbitMQ (`send`).

Запуск: python -m app.workers.send

- instant: notify_new_listings (после парсинга) → фанаут задач
  send_listing_notification (rate limit 30 сек/пользователь, тихие часы);
- digest: cron 09:00 и 20:00 UTC, 3–5 карточек одним сообщением;
- подписки: cron 12:00 UTC — напоминания за 3/1 день, даунгрейд;
- антидубли: sent_log + notified:{uid}:{lid} (TTL 7 дней) — в Redis;
- Telegram Bot API напрямую через httpx, обработка 429 (retry_after).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from redis.asyncio import Redis

from app.core.config import get_settings
from app.db.models import Listing, User, UserFilter
from app.db.session import session_factory
from app.queue import QUEUE_SEND, CronSpec, TaskPublisher, TaskWorker
from app.services.notify import (
    buffer_for_digest,
    clear_digest,
    collect_for_filters,
    format_listing_card,
    in_quiet_hours,
    mark_sent,
    read_digest,
    was_sent,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


async def send_telegram_message(
    bot_token: str, chat_id: int, text: str
) -> tuple[bool, str | None]:
    """sendMessage с parse_mode=HTML. 429 → ждём retry_after и повторяем раз."""
    if not bot_token:
        logger.warning("telegram_send_skipped: no BOT_TOKEN")
        return False, "no_bot_token"

    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
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


async def digest_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron (09:00 и 20:00 UTC): дайджест 3–5 карточек одним сообщением."""
    redis: Redis = ctx["redis"]
    bot_token: str = ctx.get("bot_token", "")
    sent = skipped = 0

    for key in list(await redis.keys("digest:*")):
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
            cards: list[str] = []
            for listing_id in ids:
                listing = await session.get(Listing, listing_id)
                if listing is None:
                    continue
                cards.append(format_listing_card(listing))

            if cards:
                header = f"📬 <b>Дайджест:</b> {len(cards)} новых вариантов\n\n"
                ok, _err = await send_telegram_message(
                    bot_token, user_id, header + "\n\n".join(cards)
                )
            else:
                ok = True  # буфер пуст по факту — просто чистим

            if ok:
                for listing_id in ids:
                    await mark_sent(session, redis, user_id, listing_id)
                sent += len(ids)
            else:
                skipped += len(ids)  # оставляем буфер до следующего прогона

        if ok:
            await clear_digest(redis, user_id)

    logger.info("digest_sent: sent=%s kept=%s", sent, skipped)
    return {"sent": sent, "kept": skipped}


async def subscription_check_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron ежедневно (12:00 UTC): напоминания за 3/1 день и даунгрейд (ТЗ п. 8.7).

    - за 3 дня и за 1 день до окончания — уведомление;
    - после окончания (не позднее 3 дней) — возврат на Free + сообщение.
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


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    # Redis (данные: дедуп, rate limit, digest-буферы, ключи подписок)
    ctx["redis"] = Redis.from_url(settings.redis_url)
    ctx["bot_token"] = settings.bot_token
    ctx["publisher"] = TaskPublisher(settings.rabbitmq_url)
    logger.info("send_worker_started")


async def shutdown(ctx: dict[str, Any]) -> None:
    publisher: TaskPublisher | None = ctx.get("publisher")
    if publisher:
        await publisher.aclose()
    redis: Redis | None = ctx.get("redis")
    if redis:
        await redis.aclose()
    logger.info("send_worker_stopped")


HANDLERS = {
    "send_listing_notification": send_listing_notification,
    "notify_new_listings": notify_new_listings,
    "digest_job": digest_job,
    "subscription_check_job": subscription_check_job,
}

# Дайджест 09:00 и 20:00 UTC, подписки — 12:00 UTC (ТЗ п. 8.6, 8.7)
CRON_JOBS = [
    CronSpec(digest_job, hour=9, minute=0),
    CronSpec(digest_job, hour=20, minute=0),
    CronSpec(subscription_check_job, hour=12, minute=0),
]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = get_settings()
    ctx: dict[str, Any] = {}
    await startup(ctx)
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
