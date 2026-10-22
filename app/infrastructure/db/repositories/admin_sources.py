"""SQLAlchemy-репозиторий админских read/write-моделей.

Источники и их здоровье, список лидов и ключ сырого payload: всё, что админка
читает и пишет напрямую. Сценарии видят только порты, поэтому SQL и коммиты
остаются здесь.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.errors import SourceConflict
from app.application.dto.admin import (
    LeadsQuery,
    SourceConfigDTO,
    SourceState,
)
from app.infrastructure.db.models import Lead, Listing, Source, SourceHealth

logger = logging.getLogger(__name__)

# Скалярные поля источника, которые переносятся из DTO в ORM как есть.
_SOURCE_FIELDS = (
    "adapter_class",
    "kind",
    "enabled",
    "priority",
    "schedule_cron",
    "rate_limit_per_min",
    "max_concurrency",
    "timeout_sec",
    "credentials_ref",
    "retry_policy",
    "capabilities",
    "config",
)


class SqlAlchemyAdminSourcesRepository:
    """Админский репозиторий источников, лидов и raw-ключей."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_sources(self) -> list[SourceConfigDTO]:
        """Все источники (включая выключенные) по приоритету."""
        rows = (
            await self._session.execute(select(Source).order_by(Source.priority))
        ).scalars().all()
        return [self._to_dto(row) for row in rows]

    async def get_source(self, name: str) -> SourceState | None:
        """Словари источника (для слияния) или `None`."""
        row = (
            await self._session.execute(select(Source).where(Source.name == name))
        ).scalar_one_or_none()
        if row is None:
            return None
        return SourceState(
            retry_policy=dict(row.retry_policy or {}),
            capabilities=dict(row.capabilities or {}),
            config=dict(row.config or {}),
        )

    async def save_source(self, config: SourceConfigDTO) -> SourceConfigDTO:
        """Создать/обновить источник, зафиксировать транзакцию.

        Args:
            config: Полный (уже смерженный) конфиг источника.

        Returns:
            Сохранённый источник.

        Raises:
            SourceConflict: гонка на создании (unique violation).
        """
        row = await self._session.get(Source, config.name)
        if row is None:
            row = Source(name=config.name)
            self._session.add(row)
        for field in _SOURCE_FIELDS:
            setattr(row, field, getattr(config, field))

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            logger.warning("source_upsert_conflict name=%s", config.name)
            raise SourceConflict(
                f"source {config.name} was modified concurrently, retry"
            ) from exc
        await self._session.refresh(row)
        return self._to_dto(row)

    async def source_health(self, name: str) -> dict[str, Any] | None:
        """Здоровье источника или `None`, если данных ещё нет."""
        health = await self._session.get(SourceHealth, name)
        if health is None:
            return None
        return {
            "source_name": health.source_name,
            "last_success_at": health.last_success_at,
            "last_error_at": health.last_error_at,
            "error_streak": health.error_streak,
            # Может содержать PII — санитизация на стороне UI.
            "last_error": health.last_error,
            "avg_latency_ms": health.avg_latency_ms,
            "total_fetched": health.total_fetched,
        }

    async def list_leads(self, query: LeadsQuery) -> list[dict[str, Any]]:
        """Лиды для админ-панели: один запрос, без ленивых догрузок."""
        conditions = []
        if query.status:
            conditions.append(Lead.status == query.status)
        if query.partner_id is not None:
            conditions.append(Lead.partner_id == query.partner_id)
        stmt = select(Lead).order_by(Lead.id.desc()).limit(query.limit)
        stmt = stmt.offset(query.offset)
        if conditions:
            stmt = stmt.where(*conditions)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            {
                "id": lead.id,
                "user_id": lead.user_id,
                "listing_id": lead.listing_id,
                "source": lead.source,
                "status": lead.status,
                "partner_id": lead.partner_id,
                "partner_status": lead.partner_status,
                "comment": lead.comment,
                "qualification": lead.qualification,
                "created_at": lead.created_at,
            }
            for lead in rows
        ]

    async def raw_payload_key(self, listing_id: int) -> str | None:
        """Ключ сырого payload объявления или `None`."""
        return (
            await self._session.execute(
                select(Listing.raw_key).where(Listing.id == listing_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    def _to_dto(row: Source) -> SourceConfigDTO:
        """ORM-строка → DTO (NULL-колонки получают дефолты)."""
        return SourceConfigDTO.model_validate(row, from_attributes=True)
