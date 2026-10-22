"""SQLAlchemy-агрегаты админ-панели для AI-помощника (только чтение)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Lead, PartnerAccount


class SqlAlchemyAdminStatsRepository:
    """Статистика лидов и партнёров на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def leads_by_status(self) -> dict[str, int]:
        """Количество лидов по статусам."""
        rows = (
            await self._session.execute(
                select(Lead.status, func.count()).group_by(Lead.status)
            )
        ).all()
        return {status: int(count) for status, count in rows}

    async def partner_feedback(self) -> dict[str, int]:
        """Количество партнёрских фидбеков по значениям `partner_status`."""
        rows = (
            await self._session.execute(
                select(Lead.partner_status, func.count()).group_by(
                    Lead.partner_status
                )
            )
        ).all()
        return {str(key): int(count) for key, count in rows if key is not None}

    async def partners(self) -> list[dict[str, Any]]:
        """Партнёры: план, дневная квота, статус."""
        accounts = (await self._session.execute(select(PartnerAccount))).scalars().all()
        return [
            {
                "id": account.id,
                "name": account.name,
                "plan": account.plan,
                "daily_quota": account.daily_quota,
                "status": account.status,
            }
            for account in accounts
        ]
