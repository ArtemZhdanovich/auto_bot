"""Правила оценки цены объявления: вердикты, диапазон, бесплатная квота.

Чистые функции без LLM, БД и сети: прикладной слой передаёт сюда готовые
числа (цена объявления, медиана рынка, число разборов) и получает решение.
Так «дорого/дёшево» можно проверить юнит-тестом без мока LLM.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Final, Literal

Verdict = Literal["deal", "fair", "overpriced"]

VERDICTS: Final[tuple[str, ...]] = ("deal", "fair", "overpriced")
# Цена ниже 93% медианы — выгодно, выше 107% — переоценено.
DEAL_RATIO: Final[float] = 0.93
OVERPRICED_RATIO: Final[float] = 1.07
FACTORS_LIMIT: Final[int] = 4

FREE_TIER: Final[str] = "free"
FREE_QUOTA_LIMIT: Final[int] = 1
FREE_QUOTA_WINDOW_DAYS: Final[int] = 7


class QuotaExceeded(Exception):
    """Бесплатный разбор этой машины на этой неделе уже израсходован."""


def market_stats(prices: list[float]) -> dict[str, float | int] | None:
    """Минимум/максимум/медиана сопоставимых цен.

    Args:
        prices: Цены объявлений той же модели (±2 года).

    Returns:
        Сводка рынка или `None`, если сопоставимых меньше двух.
    """
    if len(prices) < 2:
        return None
    return {
        "count": len(prices),
        "min": round(min(prices)),
        "max": round(max(prices)),
        "median": round(median(prices)),
    }


def verdict_from_stats(price: float, comps: dict[str, Any] | None) -> Verdict:
    """Вердикт без LLM: цена против медианы рынка.

    Args:
        price: Цена объявления.
        comps: Сводка рынка из `market_stats` (может быть `None`).

    Returns:
        `deal` / `fair` / `overpriced`; без рынка — `fair`.
    """
    if not comps:
        return "fair"
    med = float(comps.get("median") or 0)
    if not med:
        return "fair"
    ratio = price / med
    if ratio <= DEAL_RATIO:
        return "deal"
    if ratio >= OVERPRICED_RATIO:
        return "overpriced"
    return "fair"


def fair_range(comps: dict[str, Any] | None) -> tuple[float | None, float | None]:
    """Справедливый диапазон цены от медианы рынка.

    Args:
        comps: Сводка рынка из `market_stats`.

    Returns:
        Пара `(fair_min, fair_max)` или `(None, None)`, если рынка нет.
    """
    if not comps:
        return None, None
    med = float(comps.get("median") or 0)
    if not med:
        return None, None
    return round(med * DEAL_RATIO), round(med * OVERPRICED_RATIO)


def stats_factor(comps: dict[str, Any] | None) -> list[str]:
    """Фактор цены «по рынку» для UI, когда LLM не дал своих.

    Args:
        comps: Сводка рынка из `market_stats`.

    Returns:
        Список из одного текста или пустой список.
    """
    if not comps:
        return []
    return [
        f"{comps['count']} сопоставимых объявлений: "
        f"{comps['min']}–{comps['max']}, медиана {comps['median']}"
    ]


def normalize_amount(value: Any) -> float | None:
    """Привести число из ответа LLM к положительному целому.

    Args:
        value: `int`/`float`/`str`/`None` из JSON модели.

    Returns:
        Округлённая сумма или `None`, если значение не число или <= 0.
    """
    try:
        amount = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(amount) if amount > 0 else None


def quota_applies(tier: str) -> bool:
    """Ограничена ли квота на этом тарифе (free — да, pro/business — нет)."""
    return tier == FREE_TIER


def quota_left(used: int, limit: int = FREE_QUOTA_LIMIT) -> int:
    """Сколько бесплатных разборов осталось при `used` израсходованных."""
    return max(0, limit - used)


def ensure_quota(tier: str, left: int | None) -> None:
    """Проверить право на разбор.

    Args:
        tier: Тариф пользователя.
        left: Остаток бесплатных разборов (`None` — тариф без лимита).

    Raises:
        QuotaExceeded: Бесплатный лимит исчерпан.
    """
    if quota_applies(tier) and left is not None and left <= 0:
        raise QuotaExceeded
