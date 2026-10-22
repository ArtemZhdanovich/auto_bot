"""Сценарии админ-панели: источники, лиды, ассистент, raw-payload, воронки.

Транспорт вызывает только эти сценарии. SQL, коммиты, очередь и object storage
живут за портами (`AdminSourcesPort`, `AuditPort`, `RawPayloadStoragePort`),
поэтому роутер не знает ни про SQLAlchemy, ни про инфраструктуру.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import asdict
from typing import Any

from pydantic import ValidationError

from app.application.admin.assistant import AdminAssistant
from app.application.admin.errors import (
    AdminError,
    InvalidSourceConfig,
    LLMUnavailable,
    QueueUnavailable,
    RawPayloadNotFound,
    SourceConflict,
    SourceNotFound,
    StorageUnavailable,
)
from app.application.admin.dto import (
    ALLOWED_CREDENTIAL_PREFIXES,
    SOURCE_URL_KEYS,
    AssistantQuery,
    LeadsQuery,
    SourceConfigDTO,
    SourceState,
    TriggerFetchCommand,
    UpsertSourceCommand,
    is_allowed_credentials_ref,
    is_safe_external_url,
)
from app.application.admin.ports import (
    AdminSourcesPort,
    AdminStatsPort,
    AuditPort,
    RawPayloadStoragePort,
    SourceHealthPort,
)
from app.application.analytics.use_cases import FunnelDashboard, RetentionDashboard
from app.application.shared.ports import LLMPort
from app.domain.audit.ports import AuditEntry
from app.domain.search.criteria import SearchCriteria

logger = logging.getLogger(__name__)

# Сигнатура постановки задачи в очередь (job, *args) — как в воркере.
EnqueueTask = Callable[[str, str, dict[str, Any] | None, int], Awaitable[str]]
# Фабрика LLM: сбой конфигурации не должен раскрываться клиенту.
LLMFactory = Callable[[], LLMPort]
# Каталог доступных классов адаптеров.
AdapterClasses = Callable[[], Collection[str]]


# ---------------------------------------------------------------------------
# Сценарии
# ---------------------------------------------------------------------------


class ListAdapterClasses:
    """Доступные классы адаптеров (для валидации при создании источника)."""

    def __init__(self, adapters: AdapterClasses) -> None:
        self._adapters = adapters

    async def execute(self) -> list[str]:
        """Отсортированный список классов адаптеров."""
        return sorted(self._adapters())


class ListSources:
    """Все источники (включая выключенные)."""

    def __init__(self, sources: AdminSourcesPort) -> None:
        self._sources = sources

    async def execute(self) -> list[SourceConfigDTO]:
        """Список источников по приоритету."""
        return await self._sources.list_sources()


class UpsertSource:
    """Создать/обновить источник с SSRF- и credentials-валидацией."""

    def __init__(
        self,
        sources: AdminSourcesPort,
        audit: AuditPort,
        adapters: AdapterClasses,
    ) -> None:
        self._sources = sources
        self._audit = audit
        self._adapters = adapters

    async def execute(self, command: UpsertSourceCommand) -> SourceConfigDTO:
        """Слить словари с текущими значениями, проверить и сохранить.

        Args:
            command: Имя источника из пути и тело запроса.

        Returns:
            Сохранённый источник.

        Raises:
            InvalidSourceConfig: имя не совпало, неизвестный adapter_class,
                плохой `credentials_ref` или небезопасный URL в config.
            SourceConflict: источник изменён конкурентно.
        """
        body = command.config
        if body.name != command.name:
            raise InvalidSourceConfig("name mismatch")

        available = sorted(self._adapters())
        if body.adapter_class not in available:
            raise InvalidSourceConfig(
                f"adapter_class must be one of: {available}"
            )
        if not is_allowed_credentials_ref(body.credentials_ref):
            raise InvalidSourceConfig(
                "credentials_ref must start with one of "
                f"{ALLOWED_CREDENTIAL_PREFIXES}"
            )

        existing = await self._sources.get_source(command.name)
        merged = self._merge_dictionaries(body, existing)
        self._validate_urls(body.adapter_class, merged["config"])

        saved = await self._sources.save_source(body.model_copy(update=merged))
        logger.warning(
            "admin_source_upserted name=%s create=%s adapter_class=%s enabled=%s",
            saved.name,
            existing is None,
            saved.adapter_class,
            saved.enabled,
        )
        await self._record_audit(saved, created=existing is None)
        return saved

    @staticmethod
    def _merge_dictionaries(
        body: SourceConfigDTO, existing: SourceState | None
    ) -> dict[str, Any]:
        """Словари config/capabilities/retry_policy сливаются, а не затираются.

        Регрессия ParserDoctor: mvp.sh при старте перезаписывал `config`
        значением `{"page_size": 20}`, уничтожая repaired-селекторы.
        """
        merged: dict[str, Any] = {}
        for field in ("retry_policy", "capabilities", "config"):
            base = (getattr(existing, field, None) or {}) if existing else {}
            merged[field] = {**base, **(getattr(body, field) or {})}
        return merged

    @staticmethod
    def _validate_urls(adapter_class: str, config: dict[str, Any]) -> None:
        """SSRF-проверка на итоговом (смерженном) config."""
        for key in SOURCE_URL_KEYS:
            value = config.get(key)
            if isinstance(value, str) and value:
                if not is_safe_external_url(value):
                    logger.warning(
                        "admin_unsafe_url_rejected adapter_class=%s key=%s "
                        "value=%r",
                        adapter_class,
                        key,
                        value,
                    )
                    raise InvalidSourceConfig(
                        f"config.{key} must be an external http(s) URL"
                    )

    async def _record_audit(
        self, saved: SourceConfigDTO, *, created: bool
    ) -> None:
        """Audit best-effort: сбой журнала не ломает успешную операцию."""
        try:
            await self._audit.record(
                AuditEntry(
                    action="source.upsert",
                    entity="source",
                    entity_id=saved.name,
                    payload={
                        "adapter_class": saved.adapter_class,
                        "enabled": saved.enabled,
                        "created": created,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 — audit не должен ронять API
            logger.error("admin_action_log_failed: %s", exc)


class GetSourceHealth:
    """Здоровье источника: последние успехи/ошибки, latency."""

    def __init__(self, sources: AdminSourcesPort) -> None:
        self._sources = sources

    async def execute(self, name: str) -> dict[str, Any]:
        """Здоровье источника.

        Raises:
            SourceNotFound: данных ещё нет.
        """
        health = await self._sources.source_health(name)
        if health is None:
            raise SourceNotFound("no health data")
        return health


class TriggerFetch:
    """Поставить задачу парсинга источника в очередь."""

    def __init__(self, sources: AdminSourcesPort) -> None:
        self._sources = sources

    async def execute(self, command: TriggerFetchCommand) -> str:
        """Проверить источник и критерии, отдать задачу в очередь.

        Args:
            command: Источник, критерии, лимит страниц и функция постановки.

        Returns:
            `job_id` (message_id) поставленной задачи.

        Raises:
            SourceNotFound: источника нет.
            InvalidSourceConfig: критерии не проходят валидацию.
            QueueUnavailable: очередь недоступна.
        """
        payload = self._criteria_payload(command.criteria)

        source = await self._sources.get_source(command.source_name)
        if source is None:
            raise SourceNotFound(f"source {command.source_name} not found")
        try:
            return await command.enqueue(
                "fetch_source_task",
                command.source_name,
                payload,
                command.max_pages,
            )
        except Exception as exc:  # noqa: BLE001 — очередь недоступна
            logger.exception(
                "enqueue_fetch_failed source=%s", command.source_name
            )
            raise QueueUnavailable("queue unavailable, retry later") from exc

    @staticmethod
    def _criteria_payload(
        criteria: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Валидировать критерии как `SearchCriteria` и отдать payload."""
        if criteria is None:
            return None
        try:
            return SearchCriteria.model_validate(criteria).model_dump()
        except ValidationError as exc:
            raise InvalidSourceConfig("invalid search criteria") from exc


class ListLeads:
    """Лиды для админ-панели (read-only, UI/UX ТЗ §3.6)."""

    def __init__(self, sources: AdminSourcesPort) -> None:
        self._sources = sources

    async def execute(self, query: LeadsQuery) -> list[dict[str, Any]]:
        """Страница лидов по фильтрам."""
        return await self._sources.list_leads(query)


class GetListingRawPayload:
    """Presigned URL на сырой payload объявления."""

    def __init__(
        self,
        sources: AdminSourcesPort,
        storage: RawPayloadStoragePort,
        ttl_sec: int,
    ) -> None:
        self._sources = sources
        self._storage = storage
        self._ttl_sec = ttl_sec

    async def execute(self, listing_id: int) -> dict[str, Any]:
        """Ссылка на сырой payload.

        Raises:
            RawPayloadNotFound: объявления/payload нет.
            StorageUnavailable: хранилище не отдало ссылку.
        """
        key = await self._sources.raw_payload_key(listing_id)
        if key is None:
            raise RawPayloadNotFound("raw payload not found in storage")
        url = await self._storage.presign_raw(key, self._ttl_sec)
        if url is None:
            raise StorageUnavailable("storage unavailable")
        return {"key": key, "url": url, "expires_in": self._ttl_sec}


class GetFunnel:
    """Воронка search→dialog→lead→subscription (P2-бэклог)."""

    def __init__(self, dashboard: FunnelDashboard) -> None:
        self._dashboard = dashboard

    async def execute(self, days: int) -> list[dict[str, Any]]:
        """Этапы воронки в виде словарей (контракт API)."""
        stages = await self._dashboard.execute(days)
        return [asdict(stage) for stage in stages]


class GetRetention:
    """Когорты D1/D7 по дню регистрации (P2-бэклог)."""

    def __init__(self, dashboard: RetentionDashboard) -> None:
        self._dashboard = dashboard

    async def execute(self, days: int) -> list[dict[str, Any]]:
        """Когорты в виде словарей (контракт API)."""
        cohorts = await self._dashboard.execute(days)
        return [asdict(cohort) for cohort in cohorts]


class AskAssistant:
    """Один ход AI-помощника админа: LLM + tools поверх портов."""

    def __init__(self, stats: AdminStatsPort, health: SourceHealthPort) -> None:
        self._stats = stats
        self._health = health

    async def execute(self, query: AssistantQuery) -> str:
        """Ответ помощника.

        Args:
            query: Вопрос, история чата и фабрика LLM.

        Raises:
            LLMUnavailable: LLM не сконфигурирована или вызов упал.
        """
        try:
            llm = query.llm_factory()
        except Exception as exc:  # noqa: BLE001 — детали только в лог
            logger.exception("admin_assistant_llm_init_failed")
            raise LLMUnavailable("LLM unavailable") from exc

        assistant = AdminAssistant(llm, self._stats, self._health)
        try:
            return await assistant.reply(query.message, query.history)
        except Exception as exc:  # noqa: BLE001 — детали только в лог
            logger.exception("admin_assistant_llm_call_failed")
            raise LLMUnavailable("LLM request failed") from exc


__all__ = [
    "AdminError",
    "AdminSourcesPort",
    "AskAssistant",
    "AssistantQuery",
    "AuditPort",
    "GetFunnel",
    "GetListingRawPayload",
    "GetRetention",
    "GetSourceHealth",
    "InvalidSourceConfig",
    "LLMUnavailable",
    "LeadsQuery",
    "ListAdapterClasses",
    "ListLeads",
    "ListSources",
    "QueueUnavailable",
    "RawPayloadNotFound",
    "RawPayloadStoragePort",
    "SourceConflict",
    "SourceNotFound",
    "SourceState",
    "StorageUnavailable",
    "TriggerFetch",
    "TriggerFetchCommand",
    "UpsertSource",
    "UpsertSourceCommand",
]
