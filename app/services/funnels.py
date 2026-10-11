"""SQL-дашборды воронок (P2-бэклог №2) поверх analytics_events.

Два отчёта, read-only, без ПД (только агрегаты):
- funnel: конверсии search→dialog→lead→subscription за окно (distinct users);
- retention: когорты по дню регистрации, D1/D7 (возвращался ли юзер N+1/N+7).

Имена событий — канонические из app/services/analytics.py.
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Этапы воронки: (событие, метка). Диалог — любой dialog_message (апдейт ТЗ 8.4).
FUNNEL_STAGES: list[tuple[str, str]] = [
    ("search_completed", "search"),
    ("dialog_message", "dialog"),
    ("lead_created", "lead"),
    ("subscription_activated", "subscription"),
]

_FUNNEL_SQL = text("""
    SELECT name, COUNT(DISTINCT user_id) AS users
    FROM analytics_events
    WHERE name = ANY(CAST(:names AS text[]))
      AND user_id IS NOT NULL
      AND created_at >= NOW() - make_interval(days => :days)
    GROUP BY name
""")

_RETENTION_SQL = text("""
    WITH reg AS (
        SELECT user_id, MIN(created_at)::date AS reg_date
        FROM analytics_events
        WHERE name = 'user_registered' AND user_id IS NOT NULL
        GROUP BY user_id
    ),
    act AS (
        SELECT DISTINCT user_id, created_at::date AS act_date
        FROM analytics_events
        WHERE user_id IS NOT NULL
    )
    SELECT reg.reg_date AS day,
           COUNT(DISTINCT reg.user_id) AS cohort,
           COUNT(DISTINCT act.user_id)
               FILTER (WHERE act.act_date = reg.reg_date + 1) AS d1,
           COUNT(DISTINCT act.user_id)
               FILTER (WHERE act.act_date = reg.reg_date + 7) AS d7
    FROM reg
    LEFT JOIN act ON act.user_id = reg.user_id
    WHERE reg.reg_date >= CURRENT_DATE - make_interval(days => :days)
    GROUP BY reg.reg_date
    ORDER BY reg.reg_date DESC
""")


async def funnel(
    session: AsyncSession, days: int = 30
) -> list[dict[str, Any]]:
    """Воронка за окно: пользователи на каждом этапе + конверсии.

    conv_prev — к предыдущему этапу; conv_first — к первому (search).
    """
    names = [event for event, _label in FUNNEL_STAGES]
    result = await session.execute(_FUNNEL_SQL, {"names": names, "days": days})
    users_by_event = {row.name: int(row.users) for row in result.all()}

    stages: list[dict[str, Any]] = []
    first_users = 0
    prev_users = 0
    for i, (event, label) in enumerate(FUNNEL_STAGES):
        users = users_by_event.get(event, 0)
        if i == 0:
            first_users = users
        stages.append(
            {
                "stage": label,
                "event": event,
                "users": users,
                "conv_prev_pct": _pct(users, prev_users) if i > 0 else 100.0,
                "conv_first_pct": _pct(users, first_users),
            }
        )
        prev_users = users
    return stages


async def retention(
    session: AsyncSession, days: int = 30
) -> list[dict[str, Any]]:
    """Когорты по дню регистрации: размер, D1/D7 абсолютные и в процентах."""
    result = await session.execute(_RETENTION_SQL, {"days": days})
    cohorts: list[dict[str, Any]] = []
    for row in result.all():
        cohort = int(row.cohort)
        d1 = int(row.d1 or 0)
        d7 = int(row.d7 or 0)
        cohorts.append(
            {
                "day": row.day if isinstance(row.day, date) else date.fromisoformat(str(row.day)),
                "cohort": cohort,
                "d1": d1,
                "d7": d7,
                "d1_pct": _pct(d1, cohort),
                "d7_pct": _pct(d7, cohort),
            }
        )
    return cohorts


def _pct(part: float, total: float) -> float:
    return round(part / total * 100.0, 1) if total > 0 else 0.0