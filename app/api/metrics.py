"""Системные метрики мониторинга (этап 8, ТЗ п. 10): GET /metrics.

Роутер переиспользуется дважды без копирования кода:
- монолит `app/main.py` (профиль mono);
- доменный сервис `services/admin` (профиль micro) — gateway проксирует
  `/metrics` в admin, т.к. сам маршрутизирует только `/api/*`.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.deps import SessionDep
from app.core.deps_internal import check_internal_token
from app.db.models import (
    AnalyticsEvent,
    Listing,
    SentLog,
    SourceHealth,
    User,
    UserFilter,
)

router = APIRouter(tags=["system"])


async def _scalar(session: SessionDep, stmt: Any) -> int | None:
    try:
        return (await session.execute(stmt)).scalar_one()
    except Exception:  # noqa: BLE001 — метрика не может ломать эндпоинт
        return None


@router.get(
    "/metrics",
    dependencies=[Depends(check_internal_token)],  # агрегаты — не для публичного доступа
)
async def metrics(session: SessionDep) -> dict[str, Any]:
    """Базовые метрики для мониторинга (этап 8): пользователи, объявления,
    уведомления, health источников. Счётчики: агрегаты, без персональных данных."""
    day_ago = datetime.now(UTC) - timedelta(hours=24)

    users_total = await _scalar(session, select(func.count()).select_from(User))
    users_new_24h = await _scalar(
        session,
        select(func.count())
        .select_from(User)
        .where(User.created_at >= day_ago),
    )
    filters_total = await _scalar(
        session, select(func.count()).select_from(UserFilter)
    )

    listings_total = await _scalar(
        session, select(func.count()).select_from(Listing)
    )
    listings_new_24h = await _scalar(
        session,
        select(func.count())
        .select_from(Listing)
        .where(Listing.updated_at >= day_ago),
    )
    notifications_sent_24h = await _scalar(
        session,
        select(func.count()).select_from(SentLog).where(SentLog.sent_at >= day_ago),
    )

    try:
        result = await session.execute(
            select(User.subscription_tier, func.count())
            .select_from(User)
            .group_by(User.subscription_tier)
        )
        users_by_tier = {tier: int(n) for tier, n in result.all()}
    except Exception:  # noqa: BLE001 — метрика не может ломать эндпоинт
        users_by_tier = {}

    sources: list[dict[str, Any]] = []
    try:
        result = await session.execute(select(SourceHealth))
        for health in result.scalars().all():
            sources.append(
                {
                    "source_name": health.source_name,
                    "avg_latency_ms": health.avg_latency_ms,
                    "error_streak": health.error_streak,
                    "last_success_at": health.last_success_at.isoformat()
                    if health.last_success_at
                    else None,
                }
            )
    except Exception:  # noqa: BLE001
        pass

    events_24h: dict[str, int] = {}
    try:
        result = await session.execute(
            select(AnalyticsEvent.name, func.count())
            .select_from(AnalyticsEvent)
            .where(AnalyticsEvent.created_at >= day_ago)
            .group_by(AnalyticsEvent.name)
        )
        events_24h = {name: int(n) for name, n in result.all()}
    except Exception:  # noqa: BLE001
        pass

    return {
        "users": {
            "total": users_total,
            "new_24h": users_new_24h,
            "by_tier": users_by_tier,
        },
        "filters": {"total": filters_total},
        "listings": {"total": listings_total, "new_24h": listings_new_24h},
        "notifications_sent_24h": notifications_sent_24h,
        "sources": sources,
        "events_24h": events_24h,
    }
