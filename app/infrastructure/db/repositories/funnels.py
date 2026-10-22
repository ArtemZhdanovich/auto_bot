"""SQL-дашборды воронок (P2-бэклог №2) поверх analytics_events.

Два отчёта, read-only, без ПД (только агрегаты): конверсии по этапам и
retention-когорты D1/D7. Расчёты процентов — в домене
(`app.domain.analytics.funnels`), здесь только запросы.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.dto import (
    CohortRow,
    EventCount,
)

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


class SqlAlchemyFunnelRepository:
    """Агрегаты воронки и когорт по analytics_events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def users_by_event(
        self, names: Sequence[str], days: int
    ) -> list[EventCount]:
        """Distinct-пользователи по каждому событию за окно `days`."""
        result = await self._session.execute(
            _FUNNEL_SQL, {"names": list(names), "days": days}
        )
        return [
            EventCount(name=row.name, users=int(row.users))
            for row in result.all()
        ]

    async def cohorts(self, days: int) -> list[CohortRow]:
        """Когорты по дню регистрации за окно `days`."""
        result = await self._session.execute(_RETENTION_SQL, {"days": days})
        return [
            CohortRow(
                day=_as_date(row.day),
                cohort=int(row.cohort),
                d1=None if row.d1 is None else int(row.d1),
                d7=None if row.d7 is None else int(row.d7),
            )
            for row in result.all()
        ]


def _as_date(value: date | str) -> date:
    """`::date` из PG приходит датой; строку (драйвер/тест) приводим сами."""
    return value if isinstance(value, date) else date.fromisoformat(str(value))
