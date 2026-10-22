"""SQLAlchemy-репозитории источников: конфиг парсера и здоровье источников."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.dto import SourceConfig
from app.infrastructure.db.models import Source, SourceHealth


class SqlAlchemySourceRepository:
    """Конфиг источника: чтение для врача и запись отремонтированных селекторов."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, source_name: str) -> SourceConfig | None:
        """Конфиг источника или `None`, если такого источника нет."""
        source = await self._session.get(Source, source_name)
        if source is None:
            return None
        return SourceConfig(name=source.name, config=dict(source.config or {}))

    async def save_config(self, source_name: str, config: dict[str, Any]) -> None:
        """Записать новый конфиг (коммит — на стороне сценария через UoW)."""
        source = await self._session.get(Source, source_name)
        if source is None:
            return
        source.config = dict(config)
        source.updated_at = datetime.now(UTC)


class SqlAlchemySourceHealthRepository:
    """Источники и их здоровье одним запросом (для админ-панели и AI-помощника)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_with_health(self) -> list[dict[str, Any]]:
        """Список источников с полями здоровья (по приоритету)."""
        rows = (
            await self._session.execute(
                select(
                    Source.name,
                    Source.enabled,
                    SourceHealth.error_streak,
                    SourceHealth.last_error,
                    SourceHealth.avg_latency_ms,
                    SourceHealth.total_fetched,
                )
                .outerjoin(SourceHealth, SourceHealth.source_name == Source.name)
                .order_by(Source.priority)
            )
        ).all()
        return [
            {
                "name": name,
                "enabled": enabled,
                "error_streak": streak or 0,
                "last_error": last_error,
                "avg_latency_ms": avg_latency,
                "total_fetched": total,
            }
            for name, enabled, streak, last_error, avg_latency, total in rows
        ]
