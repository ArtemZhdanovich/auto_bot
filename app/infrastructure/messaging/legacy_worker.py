"""Очереди задач на RabbitMQ (миграция с ARQ/Redis).

Топология (план миграции):
- exchange `tasks` (direct, durable);
- очередь `fetch` ← routing key `fetch_source_task` (Fetch Worker);
- очередь `send` ← `send_listing_notification`, `notify_new_listings`,
  `digest_job`, `subscription_check_job` (Send Worker);
- у рабочих очередей `x-dead-letter-exchange: tasks.dlx` (fanout) →
  очередь `dead_letter`: сообщения, упавшие после 3 попыток, не теряются.

Формат сообщения: JSON {"job", "args", "kwargs", "id"} (совместимо
по семантике с arq-задачами).
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aio_pika
from aio_pika import DeliveryMode, ExchangeType

from app.config import get_settings

logger = logging.getLogger(__name__)

EXCHANGE = "tasks"
DLX_EXCHANGE = "tasks.dlx"
DLQ = "dead_letter"
QUEUE_FETCH = "fetch"
QUEUE_SEND = "send"

MAX_ATTEMPTS = 3  # попыток обработки до отправки в DLQ
RETRY_BACKOFF = 2.0  # сек между попытками (x2)


def encode_message(job: str, args: tuple, kwargs: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "id": uuid.uuid4().hex,
            "job": job,
            "args": list(args),
            "kwargs": kwargs,
        },
        ensure_ascii=False,
    ).encode()


def decode_message(body: bytes) -> dict[str, Any]:
    msg = json.loads(body)
    if "job" not in msg:
        raise ValueError("message without job")
    return msg


# --- publisher ---------------------------------------------------------------


class TaskPublisher:
    """Постановка задач: один robust-коннект на процесс, publish в exchange."""

    def __init__(self, amqp_url: str) -> None:
        self._amqp_url = amqp_url
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel = None
        self._exchange = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._exchange is not None:
            return self._exchange
        async with self._lock:
            if self._exchange is not None:
                return self._exchange
            if self._connection is None or self._connection.is_closed:
                self._connection = await aio_pika.connect_robust(self._amqp_url)
                self._channel = await self._connection.channel()
                self._exchange = await self._channel.declare_exchange(
                    EXCHANGE, ExchangeType.DIRECT, durable=True
                )
            return self._exchange

    async def enqueue(self, job: str, *args: Any, **kwargs: Any) -> str:
        """Поставить задачу. Бросает исключение, если брокер недоступен."""
        exchange = await self._ensure()
        msg_id = uuid.uuid4().hex
        message = aio_pika.Message(
            body=encode_message(job, args, kwargs),
            delivery_mode=DeliveryMode.PERSISTENT,
            message_id=msg_id,
        )
        await exchange.publish(message, routing_key=job)
        logger.info("task_enqueued: job=%s id=%s", job, msg_id)
        return msg_id

    async def enqueue_many(
        self,
        job: str,
        arg_tuples: Iterable[tuple],
        *,
        max_inflight: int = 50,
    ) -> list[str]:
        """Пакетная постановка (TECHDEBT 13.8) с ограничением параллелизма.

        Цикл `for user in users: await publisher.enqueue(...)` на 1000+
        получателей дайджеста создаёт 1000 одновременных publishes —
        брокер захлёбывается, канал душится. Semaphore ограничивает
        число незавершённых publishes значением `max_inflight`.

        Args:
            job: Имя задачи (routing key).
            arg_tuples: Список кортежей позиционных аргументов.
            max_inflight: Одновременных publishes за раз.

        Returns:
            Список message_id в порядке завершения.
        """
        semaphore = asyncio.Semaphore(max_inflight)

        async def _one(args: tuple) -> str:
            async with semaphore:
                return await self.enqueue(job, *args)

        return list(await asyncio.gather(*(_one(t) for t in arg_tuples)))

    async def aclose(self) -> None:
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
        self._connection = self._channel = self._exchange = None


_publisher: TaskPublisher | None = None


def get_publisher() -> TaskPublisher:
    """Общий publisher на процесс (API, бот, воркеры)."""
    global _publisher
    if _publisher is None:
        from app.config import get_settings

        _publisher = TaskPublisher(get_settings().rabbitmq_url)
    return _publisher


async def enqueue_task(job: str, *args: Any, **kwargs: Any) -> str:
    return await get_publisher().enqueue(job, *args, **kwargs)


# --- consumer / worker -------------------------------------------------------


@dataclass
class CronSpec:
    """Периодическая задача (замена arq.cron): ежедневно в hour:minute UTC."""

    func: Callable[..., Awaitable[Any]]
    hour: int
    minute: int

    @property
    def name(self) -> str:
        return self.func.__name__


@dataclass
class IntervalSpec:
    """Периодическая задача с фиксированным интервалом (каждые every_sec).

    Исполнение в воркере, защита от дублей между воркерами — Redis-лок
    `interval:{name}:{slot}` (слот = epoch // every_sec).
    """

    func: Callable[..., Awaitable[Any]]
    every_sec: int

    @property
    def name(self) -> str:
        return self.func.__name__


JobHandler = Callable[..., Awaitable[Any]]


class TaskWorker:
    """Консьюмер очередей: dispatch по `job`, retry, DLQ, timeout, cron.

    - concurrency: сколько задач обрабатывается одновременно (как arq max_jobs);
    - job_timeout: asyncio.wait_for на обработку;
    - после MAX_ATTEMPTS неудач — nack(requeue=False) → DLQ (не теряем);
    - cron: исполнение напрямую в воркере, защита от дублей Redis-локом
      `cron:{job}:{дата-время}` (несколько воркеров не задвоят прогон).
    """

    def __init__(
        self,
        *,
        amqp_url: str,
        bindings: dict[str, list[str]],  # queue_name -> [job names]
        handlers: dict[str, JobHandler],
        ctx: dict[str, Any],
        concurrency: int = 5,
        job_timeout: int = 120,
        cron_jobs: list[CronSpec] | None = None,
        interval_jobs: list[IntervalSpec] | None = None,
    ) -> None:
        self._amqp_url = amqp_url
        self._bindings = bindings
        self._handlers = handlers
        self._ctx = ctx
        self._concurrency = concurrency
        self._job_timeout = job_timeout
        self._cron_jobs = cron_jobs or []
        self._interval_jobs = interval_jobs or []
        self._connection = None
        self._stop = asyncio.Event()
        self._sem: asyncio.Semaphore | None = None

    async def _declare_topology(self, channel) -> dict[str, Any]:
        exchange = await channel.declare_exchange(
            EXCHANGE, ExchangeType.DIRECT, durable=True
        )
        dlx = await channel.declare_exchange(
            DLX_EXCHANGE, ExchangeType.FANOUT, durable=True
        )
        dlq = await channel.declare_queue(DLQ, durable=True)
        await dlq.bind(dlx)
        queues: dict[str, Any] = {}
        for queue_name, jobs in self._bindings.items():
            queue = await channel.declare_queue(
                queue_name,
                durable=True,
                arguments={"x-dead-letter-exchange": DLX_EXCHANGE},
            )
            for job in jobs:
                await queue.bind(exchange, routing_key=job)
            queues[queue_name] = queue
        return queues

    async def _process(self, handler: JobHandler, message) -> None:
        async with message.process(ignore_processed=True):
            try:
                msg = decode_message(message.body)
            except Exception:
                logger.exception("bad_message_dropped")
                return  # невалидный JSON — ack, иначе зациклится

            job = msg["job"]
            args, kwargs = msg.get("args", []), msg.get("kwargs", {})
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    await asyncio.wait_for(
                        handler(self._ctx, *args, **kwargs), timeout=self._job_timeout
                    )
                    return  # успех → ack внутри message.process
                except Exception:
                    logger.exception(
                        "task_failed: job=%s attempt=%s/%s", job, attempt, MAX_ATTEMPTS
                    )
                    if attempt < MAX_ATTEMPTS:
                        await asyncio.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
            # попытки исчерпаны → nack(requeue=False) → DLQ
            await message.nack(requeue=False)
            logger.error("task_to_dlq: job=%s id=%s", job, msg.get("id"))

    async def _consume_queue(self, queue, jobs: list[str]) -> None:
        handlers = {j: self._handlers[j] for j in jobs}

        async def on_message(message) -> None:
            try:
                msg = decode_message(message.body)
            except Exception:
                await message.ack()
                logger.exception("bad_message_dropped")
                return
            handler = handlers.get(msg.get("job", ""))
            if handler is None:
                await message.ack()
                logger.error("unknown_job_ack: job=%s", msg.get("job"))
                return
            # параллелизм внутри очереди ограничен семафором
            async with self._sem:
                await self._process(handler, message)

        await queue.consume(on_message)
        logger.info("consuming_queue: %s jobs=%s", queue.name, list(handlers))

    # --- cron ---------------------------------------------------------------

    async def _sleep_until_stop(self, timeout: float) -> bool:
        """True — stop установлен (выходим), False — истёк timeout.

        asyncio.Event.wait(), в отличие от threading, не принимает timeout —
        прямой вызов .wait(timeout=…) кидал TypeError и молча убивал таски
        cron/interval/monitor планировщика (create_task без ретрива).
        """
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(timeout, 0.1))
        except asyncio.TimeoutError:
            return False
        return True

    async def _cron_loop(self) -> None:
        from datetime import datetime, timedelta, timezone

        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            next_run: tuple[datetime, CronSpec] | None = None
            for spec in self._cron_jobs:
                candidate = now.replace(
                    hour=spec.hour, minute=spec.minute, second=0, microsecond=0
                )
                if candidate <= now:
                    candidate += timedelta(days=1)
                if next_run is None or candidate < next_run[0]:
                    next_run = (candidate, spec)
            if next_run is None:
                await asyncio.sleep(60)
                continue
            target, spec = next_run
            delay = (target - now).total_seconds()
            if await self._sleep_until_stop(max(delay, 0.1)):
                return
            # лок от дублей, если воркеров несколько
            lock_key = f"cron:{spec.name}:{target.strftime('%Y%m%d%H%M')}"
            redis = self._ctx.get("redis")
            if redis is not None:
                got = await redis.set(lock_key, "1", nx=True, ex=6 * 3600)
                if not got:
                    continue
            try:
                await asyncio.wait_for(spec.func(self._ctx), timeout=self._job_timeout)
                logger.info("cron_job_done: %s", spec.name)
            except Exception:
                logger.exception("cron_job_failed: %s", spec.name)

    # --- monitoring (этап 8) -------------------------------------------------

    async def _monitor_loop(self, interval: int) -> None:
        """Мониторинг DLQ: глубина >= порога → алерт (дедуп Redis в alerting)."""
        from app.infrastructure.services.alerting import send_alert

        threshold = get_settings().dlq_alert_threshold
        while not self._stop.is_set():
            if await self._sleep_until_stop(interval):
                return
            try:
                assert self._connection is not None
                channel = await self._connection.channel()
                dlq = await channel.declare_queue(DLQ, durable=True, passive=True)
                depth = dlq.declaration_result.message_count
                if depth >= threshold:
                    await send_alert(
                        f"🔴 DLQ: {depth} задач в dead_letter (порог {threshold}). "
                        f"Проверьте логи воркеров.",
                        dedup_key="dlq_depth",
                    )
                    logger.error("dlq_alert: depth=%s threshold=%s", depth, threshold)
            except Exception:  # noqa: BLE001 — мониторинг не роняет воркер
                logger.warning("dlq_monitor_failed", exc_info=True)

    # --- interval jobs -------------------------------------------------------

    async def _interval_loop(self, spec: IntervalSpec) -> None:
        from datetime import datetime, timezone

        while not self._stop.is_set():
            if await self._sleep_until_stop(max(spec.every_sec, 1)):
                return
            slot = int(datetime.now(timezone.utc).timestamp()) // spec.every_sec
            lock_key = f"interval:{spec.name}:{slot}"
            redis = self._ctx.get("redis")
            if redis is not None:
                got = await redis.set(lock_key, "1", nx=True, ex=spec.every_sec * 2)
                if not got:
                    continue  # слот уже исполнил другой воркер
            try:
                await asyncio.wait_for(
                    spec.func(self._ctx), timeout=self._job_timeout
                )
                logger.info("interval_job_done: %s", spec.name)
            except Exception:
                logger.exception("interval_job_failed: %s", spec.name)

    # --- lifecycle ----------------------------------------------------------

    async def run(self) -> None:
        self._connection = await aio_pika.connect_robust(self._amqp_url)
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=self._concurrency)
        self._sem = asyncio.Semaphore(self._concurrency)
        queues = await self._declare_topology(channel)
        for queue_name, jobs in self._bindings.items():
            await self._consume_queue(queues[queue_name], jobs)
        cron_task = asyncio.create_task(self._cron_loop()) if self._cron_jobs else None
        interval_tasks = [
            asyncio.create_task(self._interval_loop(spec))
            for spec in self._interval_jobs
        ]
        interval = get_settings().health_check_interval_sec
        monitor_task = (
            asyncio.create_task(self._monitor_loop(interval)) if interval > 0 else None
        )
        logger.info("task_worker_started: queues=%s", list(self._bindings))
        await self._stop.wait()
        for task in (cron_task, monitor_task, *interval_tasks):
            if task:
                task.cancel()
        if self._connection and not self._connection.is_closed:
            await self._connection.close()

    def stop(self) -> None:
        self._stop.set()
