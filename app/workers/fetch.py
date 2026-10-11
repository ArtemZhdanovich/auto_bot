"""Fetch Worker — этап 2 (ТЗ п. 4.6, 14.3). Очередь: RabbitMQ (`fetch`).

Запуск: python -m app.workers.fetch

Достаёт адаптер из реестра, вызывает fetch(), валидирует (Pydantic),
сохраняет в listings с дедупом, обновляет source_health, после успеха —
ставит скан фильтров в очередь `send` (notify_new_listings).
"""

import asyncio
import logging
import time
from typing import Any

from redis.asyncio import Redis

from app.adapters.registry import AdapterRegistry
from app.core.config import get_settings
from app.db.session import session_factory
from app.queue import QUEUE_FETCH, TaskWorker, enqueue_task
from app.schemas.criteria import SearchCriteria
from app.services.fetch import save_listings
from app.services.source_health import record_error, record_success, should_skip_source

logger = logging.getLogger(__name__)


async def fetch_source_task(
    ctx: dict[str, Any],
    source_name: str,
    criteria: dict[str, Any] | None = None,
    max_pages: int = 3,
) -> dict[str, Any]:
    """Задача: выкачать порцию объявлений с источника и сохранить."""
    registry: AdapterRegistry = ctx["registry"]
    redis: Redis = ctx["redis"]

    async with session_factory() as session:
        if await should_skip_source(session, source_name):
            logger.warning("source_skipped_circuit_breaker: %s", source_name)
            return {"source": source_name, "skipped": True}

        criteria_obj = SearchCriteria(**criteria) if criteria else SearchCriteria()

        try:
            adapter = registry.get(source_name)
        except KeyError:
            logger.error("adapter_not_loaded: %s", source_name)
            return {"source": source_name, "error": "adapter not loaded"}

        saved = in_source_dup = cross_dup = 0
        cursor: str | None = None
        pages = 0
        try:
            while pages < max_pages:
                started = time.perf_counter()
                result = await adapter.fetch(criteria_obj, cursor)
                latency_ms = int((time.perf_counter() - started) * 1000)

                stats = await save_listings(session, redis, result.items)
                saved += stats["saved"]
                in_source_dup += stats["in_source_dup"]
                cross_dup += stats["cross_dup"]

                await record_success(
                    session, source_name, latency_ms=latency_ms, fetched=len(result.items)
                )
                pages += 1

                if not result.has_more or not result.next_cursor:
                    break
                cursor = result.next_cursor
        except Exception as exc:
            logger.exception("fetch_failed: %s", source_name)
            await record_error(session, source_name, str(exc))
            return {
                "source": source_name,
                "error": str(exc),
                "saved": saved,
                "pages": pages,
            }

        if saved:
            await enqueue_notify_scan(ctx, source_name)

        return {
            "source": source_name,
            "pages": pages,
            "saved": saved,
            "in_source_dup": in_source_dup,
            "cross_dup": cross_dup,
        }


async def enqueue_notify_scan(ctx: dict[str, Any], source_name: str) -> bool:
    """После парсинга — скан фильтров уведомлений (ТЗ п. 8.6 instant).

    Не критично для парсинга: ошибка очереди не ломает fetch.
    """
    try:
        await enqueue_task("notify_new_listings", source_name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("enqueue_notify_scan_failed: %s", exc)
        return False


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    registry = AdapterRegistry(session_factory)
    await registry.load()
    ctx["registry"] = registry
    ctx["redis"] = Redis.from_url(settings.redis_url)
    logger.info("fetch_worker_started: sources=%s", [a.source_name for a in registry.all()])


async def shutdown(ctx: dict[str, Any]) -> None:
    registry: AdapterRegistry | None = ctx.get("registry")
    if registry:
        await registry.aclose()
    redis: Redis | None = ctx.get("redis")
    if redis:
        await redis.aclose()
    logger.info("fetch_worker_stopped")


HANDLERS = {"fetch_source_task": fetch_source_task}


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = get_settings()
    ctx: dict[str, Any] = {}
    await startup(ctx)
    worker = TaskWorker(
        amqp_url=settings.rabbitmq_url,
        bindings={QUEUE_FETCH: list(HANDLERS)},
        handlers=HANDLERS,
        ctx=ctx,
        concurrency=3,   # как arq max_jobs=3
        job_timeout=120,  # как arq job_timeout
    )
    try:
        await worker.run()
    finally:
        await shutdown(ctx)


if __name__ == "__main__":
    asyncio.run(main())
