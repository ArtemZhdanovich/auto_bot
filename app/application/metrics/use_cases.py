"""Сценарий снимка метрик мониторинга (ТЗ п. 10).

Собирает агрегаты через репозиторий и отдаёт транспорту готовый снимок.
Ни SQL, ни HTTP здесь нет: роутер только сериализует результат и кэширует его.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

# Top-N событий за 24h: не все возможные имена при высокой кардинальности.
EVENTS_LIMIT = 50
# Кэш: Prometheus скрэйтит /metrics каждые 15s, COUNT(*) на больших таблицах
# дороже — снимок живёт 30s (см. роутер).
WINDOW_HOURS = 24


@dataclass(frozen=True, slots=True)
class CountMetrics:
    """Пара «всего / за окно» для сущности."""

    total: int | None = None
    new_24h: int | None = None


@dataclass(frozen=True, slots=True)
class SourceHealthMetrics:
    """Здоровье одного источника в снимке."""

    source_name: str
    avg_latency_ms: int | None = None
    error_streak: int | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Снимок метрик для ответа `/metrics`."""

    generated_at: datetime
    users_total: int | None = None
    users_new_24h: int | None = None
    users_by_tier: dict[str, int] = field(default_factory=dict)
    filters: CountMetrics = field(default_factory=CountMetrics)
    listings: CountMetrics = field(default_factory=CountMetrics)
    leads: CountMetrics = field(default_factory=CountMetrics)
    price_analyses: CountMetrics = field(default_factory=CountMetrics)
    notifications_sent_24h: int | None = None
    sources: list[SourceHealthMetrics] = field(default_factory=list)
    events_24h: dict[str, int] = field(default_factory=dict)


class MetricsPort(Protocol):
    """Источник агрегатов для снимка (реализация — SQLAlchemy)."""

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


class GetMetricsSnapshot:
    """Собрать снимок метрик за окно 24h."""

    def __init__(self, metrics: MetricsPort) -> None:
        self._metrics = metrics

    async def db_available(self) -> bool:
        """Проверка БД: `False` → транспорт отдаёт 503."""
        return await self._metrics.db_available()

    async def execute(self) -> MetricsSnapshot:
        """Снимок метрик.

        Запросы идут последовательно: сессия SQLAlchemy не поддерживает
        параллельное использование одним соединением.
        """
        now = datetime.now(UTC)
        since = now - timedelta(hours=WINDOW_HOURS)

        users_total, users_new = await self._metrics.user_totals(since)
        filters_total = await self._metrics.user_filter_total()
        listings_total, listings_new = await self._metrics.listing_totals(since)
        leads_total, leads_new = await self._metrics.lead_totals(since)
        analyses_total, analyses_new = await self._metrics.price_analysis_totals(
            since
        )
        notifications = await self._metrics.notifications_sent(since)
        by_tier = await self._metrics.users_by_tier()
        events = await self._metrics.events_since(since, EVENTS_LIMIT)
        sources = await self._metrics.sources_health()

        return MetricsSnapshot(
            generated_at=now,
            users_total=users_total,
            users_new_24h=users_new,
            users_by_tier=by_tier,
            filters=CountMetrics(total=filters_total),
            listings=CountMetrics(total=listings_total, new_24h=listings_new),
            leads=CountMetrics(total=leads_total, new_24h=leads_new),
            price_analyses=CountMetrics(
                total=analyses_total, new_24h=analyses_new
            ),
            notifications_sent_24h=notifications,
            sources=[
                SourceHealthMetrics(
                    source_name=item["source_name"],
                    avg_latency_ms=item["avg_latency_ms"],
                    error_streak=item["error_streak"],
                    last_success_at=_isoformat(item["last_success_at"]),
                    last_error_at=_isoformat(item["last_error_at"]),
                )
                for item in sources
            ],
            events_24h=events,
        )


def _isoformat(value: datetime | None) -> str | None:
    """`datetime` → ISO-строка (контракт ответа)."""
    return value.isoformat() if value is not None else None


__all__ = [
    "CountMetrics",
    "GetMetricsSnapshot",
    "MetricsPort",
    "MetricsSnapshot",
    "SourceHealthMetrics",
]
