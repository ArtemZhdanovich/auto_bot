"""Порты админ-контура: панель, аналитика, метрики, ParserDoctor, алерты.

Реализации — в `infrastructure/` (SQLAlchemy, S3, LLM, Redis, Telegram).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from app.application.admin.dto import (
    CohortRow,
    EventCount,
    LeadsQuery,
    ListPage,
    SourceConfig,
    SourceConfigDTO,
    SourceState,
)
from app.domain.audit.ports import AuditEntry


class AdminStatsPort(Protocol):
    """Агрегаты админ-панели, которые помощник показывает модели."""

    async def leads_by_status(self) -> dict[str, int]:
        """Количество лидов по статусам."""

    async def partner_feedback(self) -> dict[str, int]:
        """Количество партнёрских фидбеков по значениям `partner_status`."""

    async def partners(self) -> list[dict[str, Any]]:
        """Партнёры: план, дневная квота, статус."""


class SourceHealthPort(Protocol):
    """Источники и их здоровье."""

    async def list_with_health(self) -> list[dict[str, Any]]:
        """Источники с ошибками подряд, латентностью и объёмом."""


class AdminSourcesPort(Protocol):
    """Админский read-model: источники, их здоровье, лиды, raw-ключи."""

    async def list_sources(self) -> list[SourceConfigDTO]:
        """Все источники, включая выключенные."""

    async def get_source(self, name: str) -> SourceState | None:
        """Словари источника по имени или `None`."""

    async def save_source(self, config: SourceConfigDTO) -> SourceConfigDTO:
        """Создать/обновить источник и зафиксировать транзакцию."""

    async def source_health(self, name: str) -> dict[str, Any] | None:
        """Здоровье источника или `None`, если данных ещё нет."""

    async def list_leads(self, query: LeadsQuery) -> list[dict[str, Any]]:
        """Лиды для админ-панели (read-only)."""

    async def raw_payload_key(self, listing_id: int) -> str | None:
        """Ключ сырого payload объявления или `None`."""


class RawPayloadStoragePort(Protocol):
    """Object storage: короткоживущая ссылка на сырой payload."""

    async def presign_raw(self, key: str, ttl: int) -> str | None:
        """Presigned GET или `None`, если хранилище недоступно/выключено."""


class AuditPort(Protocol):
    """Журнал админ-действий (UI/UX ТЗ §3.9)."""

    async def record(self, entry: AuditEntry) -> None:
        """Записать действие; политику «не ломать поток» задаёт сценарий."""


class AnalyticsRepository(Protocol):
    """Запись событий воронки."""

    async def add_event(
        self,
        *,
        user_id: int | None,
        name: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        """Записать событие; реализация коммитит сама, ошибку пробрасывает."""


class FunnelRepository(Protocol):
    """Read-only агрегаты по analytics_events (без ПД, только счётчики)."""

    async def users_by_event(
        self, names: Sequence[str], days: int
    ) -> list[EventCount]:
        """Distinct-пользователи по каждому событию за окно `days`."""

    async def cohorts(self, days: int) -> list[CohortRow]:
        """Когорты по дню регистрации за окно `days` (D1/D7)."""


class MetricsPort(Protocol):
    """Источник агрегатов для снимка `/metrics` (реализация — SQLAlchemy)."""

    async def db_available(self) -> bool:
        """Отвечает ли БД на пробный запрос."""

    async def user_totals(
        self, since: datetime
    ) -> tuple[int | None, int | None]:
        """Пользователи: всего и новые."""

    async def user_filter_total(self) -> int | None:
        """Всего фильтров."""

    async def listing_totals(
        self, since: datetime
    ) -> tuple[int | None, int | None]:
        """Объявления: всего и новые."""

    async def lead_totals(
        self, since: datetime
    ) -> tuple[int | None, int | None]:
        """Лиды: всего и новые."""

    async def price_analysis_totals(
        self, since: datetime
    ) -> tuple[int | None, int | None]:
        """Ценовые разборы: всего и новые."""

    async def notifications_sent(self, since: datetime) -> int | None:
        """Отправленные уведомления."""

    async def users_by_tier(self) -> dict[str, int]:
        """Пользователи по тарифам."""

    async def events_since(
        self, since: datetime, limit: int
    ) -> dict[str, int]:
        """Top-N событий за окно."""

    async def sources_health(self) -> list[dict[str, Any]]:
        """Здоровье источников."""


class SourceConfigRepository(Protocol):
    """Конфиг источника: чтение и запись отремонтированных селекторов."""

    async def get(self, source_name: str) -> SourceConfig | None:
        """Конфиг источника или `None`, если источника нет."""

    async def save_config(self, source_name: str, config: dict[str, Any]) -> None:
        """Записать новый конфиг в текущую транзакцию."""


class ParserProbe(Protocol):
    """Живая страница списка и проверка селекторов на её HTML."""

    async def fetch_list_page(self, config: dict[str, Any]) -> ListPage:
        """Забрать страницу списка тем же HTTP-клиентом, что и парсер."""

    def validate_selectors(self, config: dict[str, Any], html: str) -> int | None:
        """Спарсить HTML с селекторами: число карточек или `None`."""


class SourceRegistryPort(Protocol):
    """Перезагрузка адаптера источника после смены конфига."""

    async def reload(self, source_name: str) -> None:
        """Пересобрать адаптер из нового конфига."""


class AlertPort(Protocol):
    """Алерт владельцу (Telegram) с дедупликацией."""

    async def send(self, text: str, *, dedup_key: str) -> None:
        """Отправить текст; повтор с тем же ключом подавляется."""


class LockPort(Protocol):
    """Именованный лок с TTL (Redis `SET NX EX`)."""

    async def acquire(self, key: str, ttl_sec: int) -> bool:
        """True — ключ наш, False — уже занят/на cooldown."""

    async def release(self, key: str) -> None:
        """Снять ключ (идемпотентно)."""


class NotifierPort(Protocol):
    """Отправка текста пользователю (Telegram)."""

    async def send_text(self, chat_id: int, text: str) -> bool: ...

