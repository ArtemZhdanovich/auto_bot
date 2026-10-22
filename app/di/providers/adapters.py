"""Провайдеры сквозных адаптеров: порты → Redis/LLM/RabbitMQ.

Адаптеры переехали из `transport/http/deps/use_cases.py`: транспорт больше
не собирает граф объектов — только контейнер. `_llm` остался функцией
модуля, чтобы тесты подменяли LLM одной строкой (`monkeypatch`).
"""

from __future__ import annotations

import logging
from typing import Any

from dishka import Provider, Scope, provide
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.use_cases import (
    AdapterClasses,
    RawPayloadStoragePort,
)
from app.application.analytics.use_cases import TrackEvent
from app.application.analysis.use_cases import LLMFactory, UserLookupPort
from app.application.dialog.use_cases import (
    AiUnavailable,
    DialogAgentPort,
    DialogAnalyticsPort,
    DialogStorePort,
    FetchQueuePort,
    TriggerFetch,
    UserProfilePort,
)
from app.application.dto.dialog import DialogReply, UserProfile
from app.application.notifications.settings import Enqueuer
from app.application.partners.queries import PartnerRateLimiter
from app.application.ports import CachePort, LLMPort, QuotaStore
from app.application.shared.ports import EventTracker, IdempotencyGuard
from app.config import Settings, get_settings
from app.domain.subscription.policies import effective_tier
from app.infrastructure.ai.agent import DialogError, run_agent
from app.infrastructure.ai.llm_client import LLMError, get_cached_llm_client
from app.infrastructure.cache.dialog_store import RedisDialogStore
from app.infrastructure.cache.quota import RedisQuotaStore
from app.infrastructure.cache.redis import RedisCache
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories.analytics import (
    SqlAlchemyAnalyticsRepository,
)
from app.infrastructure.messaging.legacy_worker import enqueue_task
from app.infrastructure.services.analytics import EVENT_DIALOG_MESSAGE, track
from app.infrastructure.services.partners.rate_limit import RedisRateLimiter
from app.infrastructure.sources import ADAPTER_CLASSES
from app.infrastructure.storage.s3 import storage_from_settings

logger = logging.getLogger(__name__)



def _llm() -> LLMPort:
    """Кэшированный LLM-клиент (порт); ошибка конфигурации → `AiUnavailable`."""
    settings = get_settings()
    try:
        return get_cached_llm_client(
            settings.llm_provider, settings.llm_default_model
        )
    except LLMError as exc:
        logger.exception("llm_init_failed")
        raise AiUnavailable() from exc


class LegacyTaskEnqueuer:
    """Порт `Enqueuer` → `legacy_worker.enqueue_task` (RabbitMQ)."""

    async def enqueue(self, job: str) -> str:
        """Поставить задание в очередь."""
        return await enqueue_task(job)


class RedisIdempotencyGuard:
    """Порт `IdempotencyGuard` на Redis `SET NX EX`."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def acquire(self, key: str, ttl_sec: int) -> bool:
        """Занять ключ; `False` — ключ уже занят."""
        return bool(await self._redis.set(key, "1", nx=True, ex=ttl_sec))

    async def release(self, key: str) -> None:
        """Снять ключ (задача не поставлена — не блокируем повтор)."""
        await self._redis.delete(key)


class UserProfileAdapter:
    """`UserProfilePort`/`UserLookupPort` поверх `users` (ORM не покидает адаптер)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int) -> UserProfile | None:
        user = await self._session.get(User, user_id)
        if user is None:
            return None
        return UserProfile(
            user_id=user.telegram_id,
            is_active=bool(user.is_active),
            tier=effective_tier(user),
            email=getattr(user, "email", None),
        )


class DialogAnalyticsAdapter:
    """`DialogAnalyticsPort` → воронка `analytics`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def dialog_message(self, user_id: int) -> None:
        await track(self._session, user_id, EVENT_DIALOG_MESSAGE)


class FetchQueueAdapter:
    """`FetchQueuePort` → RabbitMQ `fetch_source_task`."""

    async def enqueue_source(self, name: str, payload: dict[str, Any]) -> None:
        await enqueue_task("fetch_source_task", name, payload, 1)


class DialogAgentAdapter:
    """`DialogAgentPort` → `run_agent` (LLM, Redis-состояние, tools)."""

    def __init__(
        self,
        session: AsyncSession,
        redis: Any,
        llm_factory: LLMFactory,
        trigger: TriggerFetch,
    ) -> None:
        self._session = session
        self._redis = redis
        self._llm_factory = llm_factory
        self._trigger = trigger

    async def reply(
        self, user_id: int, text: str, *, quota_limit: int | None = None
    ) -> DialogReply:
        try:
            reply = await run_agent(
                user_id,
                text,
                session=self._session,
                redis=self._redis,
                llm=self._llm_factory(),
                finalize=self._trigger.execute,
                quota_limit=quota_limit,
            )
        except DialogError as exc:
            logger.exception("dialog_agent_failed user_id=%s", user_id)
            raise AiUnavailable() from exc
        return DialogReply(
            session_id=reply.session_id,
            text=reply.text,
            options=tuple(reply.options),
            status=reply.status,
            finalize_ready=reply.finalize_ready,
            turn_count=reply.turn_count,
            criteria=dict(reply.criteria),
        )


class AdapterProvider(Provider):
    """Сквозные порты: guard/enqueuer/LLM/хранилища на Redis и S3."""

    scope = Scope.REQUEST

    @provide
    def idempotency_guard(self, redis: Redis) -> IdempotencyGuard:
        """`IdempotencyGuard` → Redis `SET NX EX`.

        Args:
            redis: Клиент Redis приложения.

        Returns:
            Guard дедупликации ручного скана.
        """
        return RedisIdempotencyGuard(redis)

    @provide
    def partner_rate_limiter(
        self, redis: Redis, settings: Settings
    ) -> PartnerRateLimiter:
        """`PartnerRateLimiter` → фиксированное окно на Redis.

        Args:
            redis: Клиент Redis приложения.
            settings: Настройки (лимит запросов в минуту).

        Returns:
            Rate-limit партнёрского API (fail-open при отказе Redis).
        """
        return RedisRateLimiter(redis, settings)

    @provide
    def task_enqueuer(self) -> Enqueuer:
        """`Enqueuer` → legacy-очередь задач (RabbitMQ).

        Returns:
            Поставщик заданий `scan_filters_task`.
        """
        return LegacyTaskEnqueuer()

    @provide
    def llm_factory(self) -> LLMFactory:
        """Фабрика LLM для сценариев (кэш `(provider, model)` — в клиенте).

        Returns:
            Фабрика `_llm` (подменяется в тестах через monkeypatch).
        """
        return _llm

    @provide
    def user_profile_port(self, session: AsyncSession) -> UserProfilePort:
        """`UserProfilePort` → адаптер поверх таблицы `users`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Чтение профиля для диалога.
        """
        return UserProfileAdapter(session)

    @provide
    def user_lookup_port(self, session: AsyncSession) -> UserLookupPort:
        """`UserLookupPort` (analysis) → тот же адаптер профиля.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Чтение профиля для AI-разбора цены.
        """
        return UserProfileAdapter(session)

    @provide
    def dialog_analytics_port(self, session: AsyncSession) -> DialogAnalyticsPort:
        """`DialogAnalyticsPort` → событие воронки `dialog_message`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Запись события диалога в аналитику.
        """
        return DialogAnalyticsAdapter(session)

    @provide
    def fetch_queue(self) -> FetchQueuePort:
        """`FetchQueuePort` → очередь парсинга (RabbitMQ).

        Returns:
            Поставщик задач `fetch_source_task`.
        """
        return FetchQueueAdapter()

    @provide
    def dialog_agent(
        self,
        session: AsyncSession,
        redis: Redis,
        llm_factory: LLMFactory,
        trigger: TriggerFetch,
    ) -> DialogAgentPort:
        """`DialogAgentPort` → `run_agent` поверх сессии/Redis/LLM.

        Args:
            session: Сессия текущего запроса.
            redis: Клиент Redis (состояние диалога).
            llm_factory: Фабрика LLM-клиента.
            trigger: Сценарий постановки парсинга после финализации.

        Returns:
            Адаптер хода агента.
        """
        return DialogAgentAdapter(session, redis, llm_factory, trigger)

    @provide
    def dialog_store(self, redis: Redis) -> DialogStorePort:
        """`DialogStorePort` → Redis-хранилище состояния диалога.

        Args:
            redis: Клиент Redis приложения.

        Returns:
            Хранилище состояния диалога.
        """
        return RedisDialogStore(redis)

    @provide
    def message_cache(self, redis: Redis) -> CachePort:
        """`CachePort` лимитера сообщений → `RedisCache`.

        Args:
            redis: Клиент Redis приложения.

        Returns:
            Кэш per-user лимита сообщений.
        """
        return RedisCache(redis)

    @provide
    def quota_store(self, redis: Redis) -> QuotaStore:
        """`QuotaStore` AI-квоты → `RedisQuotaStore`.

        Args:
            redis: Клиент Redis приложения.

        Returns:
            Дневная квота AI-поиска.
        """
        return RedisQuotaStore(redis)

    @provide
    def track_event(self, session: AsyncSession) -> TrackEvent:
        """Сценарий записи события воронки (конкретный класс).

        Args:
            session: Сессия текущего запроса.

        Returns:
            `TrackEvent` на аналитическом репозитории сессии.
        """
        return TrackEvent(SqlAlchemyAnalyticsRepository(session))

    @provide
    def optional_event_tracker(self, tracker: EventTracker) -> EventTracker | None:
        """Тот же трекер под union-ключом (у сценариев параметр опционален).

        Args:
            tracker: Обязательный трекер событий.

        Returns:
            Тот же колбэк (`EventTracker | None`).
        """
        return tracker

    @provide
    def event_tracker(self, session: AsyncSession) -> EventTracker:
        """Порт `EventTracker` → `TrackEvent.execute` (claim/платежи).

        Args:
            session: Сессия текущего запроса.

        Returns:
            Колбэк записи события.
        """
        return TrackEvent(SqlAlchemyAnalyticsRepository(session)).execute

    @provide(scope=Scope.APP)
    def raw_payload_storage(self) -> RawPayloadStoragePort:
        """`RawPayloadStoragePort` → S3-хранилище из настроек.

        Returns:
            Клиент объектного хранилища (presign сырых payload).
        """
        return storage_from_settings()

    @provide(scope=Scope.APP)
    def adapter_classes(self) -> AdapterClasses:
        """Каталог классов адаптеров (ключи `ADAPTER_CLASSES`).

        Returns:
            Колбэк, возвращающий коллекцию имён классов.
        """

        def _classes() -> AdapterClasses:
            return ADAPTER_CLASSES

        return _classes

# placeholder
