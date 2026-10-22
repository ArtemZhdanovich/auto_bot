"""Чистые расчёты дашбордов воронки (P2-бэклог №2): конверсии и retention.

Ни SQL, ни обращений к «сейчас»: на вход приходят уже посчитанные агрегаты
из репозитория, на выходе — типизированные строки отчёта.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from app.domain.analytics.events import (
    EVENT_DIALOG_MESSAGE,
    EVENT_LEAD_CREATED,
    EVENT_SEARCH_COMPLETED,
    EVENT_SUBSCRIPTION_ACTIVATED,
)

# Этапы воронки: (событие, метка). Диалог — любой dialog_message (апдейт ТЗ 8.4).
FUNNEL_STAGES: tuple[tuple[str, str], ...] = (
    (EVENT_SEARCH_COMPLETED, "search"),
    (EVENT_DIALOG_MESSAGE, "dialog"),
    (EVENT_LEAD_CREATED, "lead"),
    (EVENT_SUBSCRIPTION_ACTIVATED, "subscription"),
)


@dataclass(frozen=True, slots=True)
class FunnelStage:
    """Этап воронки с конверсиями."""

    stage: str
    event: str
    users: int
    conv_prev_pct: float
    conv_first_pct: float


@dataclass(frozen=True, slots=True)
class RetentionCohort:
    """Когорта по дню регистрации: размер, D1/D7 абсолютные и в процентах."""

    day: date
    cohort: int
    d1: int
    d7: int
    d1_pct: float
    d7_pct: float


def pct(part: float, total: float) -> float:
    """Доля в процентах с одним знаком; пустая база — 0.0."""
    return round(part / total * 100.0, 1) if total > 0 else 0.0


def funnel_stages(users_by_event: Mapping[str, int]) -> list[FunnelStage]:
    """Собрать этапы воронки с конверсиями.

    Порядок этапов задаёт `FUNNEL_STAGES`, а не порядок строк из БД;
    отсутствующий этап считается нулём. `conv_prev_pct` первого этапа —
    база (100.0), `conv_first_pct` — к первому этапу.

    Args:
        users_by_event: Distinct-пользователи по имени события.

    Returns:
        Этапы в порядке воронки.
    """
    stages: list[FunnelStage] = []
    first_users = 0
    prev_users = 0
    for index, (event, label) in enumerate(FUNNEL_STAGES):
        users = int(users_by_event.get(event, 0))
        if index == 0:
            first_users = users
        stages.append(
            FunnelStage(
                stage=label,
                event=event,
                users=users,
                conv_prev_pct=pct(users, prev_users) if index > 0 else 100.0,
                conv_first_pct=pct(users, first_users),
            )
        )
        prev_users = users
    return stages


def retention_cohorts(
    rows: Iterable[tuple[date, int, int | None, int | None]],
) -> list[RetentionCohort]:
    """Когорты: строки `(день, размер, d1, d7)` → отчёт с процентами.

    `None` в d1/d7 (неделя ещё не прошла) считается нулём.
    """
    cohorts: list[RetentionCohort] = []
    for day, cohort, d1, d7 in rows:
        size = int(cohort)
        returned_d1 = int(d1 or 0)
        returned_d7 = int(d7 or 0)
        cohorts.append(
            RetentionCohort(
                day=day,
                cohort=size,
                d1=returned_d1,
                d7=returned_d7,
                d1_pct=pct(returned_d1, size),
                d7_pct=pct(returned_d7, size),
            )
        )
    return cohorts
