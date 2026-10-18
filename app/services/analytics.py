"""Продуктовая аналитика (P1-7): события воронки в analytics_events.

track() никогда не ломает основной поток: ошибка записи события — warning
в логе, бизнес-операция продолжается (тот же принцип, что в alerting).
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalyticsEvent

logger = logging.getLogger(__name__)

# Канонические события воронки
EVENT_USER_REGISTERED = "user_registered"
EVENT_SEARCH_COMPLETED = "search_completed"
EVENT_DIALOG_MESSAGE = "dialog_message"
EVENT_AI_SEARCH = "ai_search"  # выдача результата AI-подбора (preview/finalize)
EVENT_LEAD_CREATED = "lead_created"  # создатель лидов появится позже — зарезервировано
EVENT_LEAD_SOLD = "partner_lead_claimed"  # P2-10: лид забран партнёром (CPL)
EVENT_LEAD_FEEDBACK = "partner_lead_feedback"  # P2-10: фидбек партнёра по качеству
EVENT_SUBSCRIPTION_ACTIVATED = "subscription_activated"


async def track(
    session: AsyncSession,
    user_id: int | None,
    name: str,
    props: dict[str, Any] | None = None,
) -> None:
    """Записать событие воронки. Ошибка записи не бросается наружу."""
    try:
        session.add(AnalyticsEvent(user_id=user_id, name=name, props=props))
        await session.commit()
    except Exception:  # noqa: BLE001 — аналитика не ломает бизнес-поток
        logger.warning("analytics_track_failed: %s", name, exc_info=True)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
