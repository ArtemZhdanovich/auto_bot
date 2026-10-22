"""DTO дашбордов воронки: строки, которые слой ждёт от хранилища.

Пишущая сторона (`TrackEvent`) DTO не требует — событие описывается
именем и props. Здесь только агрегаты для отчётов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class EventCount:
    """Сколько distinct-пользователей сделали событие."""

    name: str
    users: int


@dataclass(frozen=True, slots=True)
class CohortRow:
    """Агрегат по когорте: размер и вернувшиеся на D1/D7."""

    day: date
    cohort: int
    d1: int | None = None
    d7: int | None = None
