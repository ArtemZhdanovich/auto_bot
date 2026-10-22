"""Построение критериев поиска и их ослабление (ТЗ п. 5.2, 5.5).

Слой описывает *что* фильтровать, но не *как*: SQL-выражения собирает
репозиторий поиска, здесь только чистые правила и условия-данные.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.domain.search.criteria import SearchCriteria

# Операторы сравнения: репозиторий переводит их в выражения SQL.
OP_ILIKE = "ilike"
OP_GTE = "gte"
OP_LTE = "lte"
OP_IN = "in"


@dataclass(frozen=True, slots=True)
class FilterTerm:
    """Одно условие запроса: колонка, оператор и значение."""

    column: str
    op: str
    value: Any


# Источники, у которых страна известна без отдельной колонки (этап 5):
# в listings страна не хранится, KR — единственный поддержанный случай.
KOREAN_SOURCES: tuple[str, ...] = ("encar", "kcar")


def listing_filters(criteria: SearchCriteria) -> list[FilterTerm]:
    """Условия запроса объявлений в порядке применения.

    Нераспознанные адаптером значения спеков лежат «как есть», поэтому
    `ilike` ловит и канон ('suv'), и сырое значение источника.
    """
    terms: list[FilterTerm] = []
    if criteria.make:
        terms.append(FilterTerm("make", OP_ILIKE, criteria.make))
    if criteria.model:
        terms.append(FilterTerm("model", OP_ILIKE, criteria.model))
    if criteria.year_from:
        terms.append(FilterTerm("year", OP_GTE, criteria.year_from))
    if criteria.year_to:
        terms.append(FilterTerm("year", OP_LTE, criteria.year_to))
    if criteria.price_min:
        terms.append(FilterTerm("price", OP_GTE, criteria.price_min))
    if criteria.price_max:
        terms.append(FilterTerm("price", OP_LTE, criteria.price_max))
    if criteria.mileage_max:
        terms.append(FilterTerm("mileage_km", OP_LTE, criteria.mileage_max))
    # Спеки в listings (этап 5, миграция 0016): кузов/топливо/КПП/привод/цвет.
    for field in ("body_type", "fuel", "transmission", "drive", "color"):
        value = getattr(criteria, field)
        if value:
            terms.append(FilterTerm(field, OP_ILIKE, value))
    if criteria.country:
        # Источник знает страну через capabilities; уточняется в этапе 5.
        terms.append(FilterTerm("source", OP_IN, KOREAN_SOURCES))
    if criteria.sources:
        terms.append(FilterTerm("source", OP_IN, tuple(criteria.sources)))
    return terms


# Порядок ослабления критериев («пусто по точным — покажи близкие»):
# сначала жертвуем косметикой (цвет), потом пробегом, спеками, годами, ценой.
RELAX_STEPS: tuple[tuple[str, str], ...] = (
    ("color", "цвет"),
    ("mileage_max", "пробег"),
    ("body_type", "кузов"),
    ("fuel", "топливо"),
    ("transmission", "КПП"),
    ("drive", "привод"),
    ("year", "год"),
    ("price_max", "бюджет +20%"),
)

# Шаги, которые просто снимают параметр.
_DROP_FIELDS = frozenset(
    {"color", "mileage_max", "body_type", "fuel", "transmission", "drive"}
)


def relaxed(criteria: SearchCriteria, step_name: str) -> SearchCriteria:
    """Копия критериев с ослаблением одного параметра.

    Если ослаблять нечего (параметр не задан), возвращаются исходные
    критерии — вызывающий пропускает такой шаг, сравнивая объекты.

    Args:
        criteria: Критерии пользователя (не мутируются).
        step_name: Имя шага из `RELAX_STEPS`.

    Returns:
        Новые критерии либо тот же объект, если шаг неприменим.
    """
    update: dict[str, object] = {}
    if step_name in _DROP_FIELDS:
        update[step_name] = None
    elif step_name == "year":
        if criteria.year_from:
            update["year_from"] = max(criteria.year_from - 3, 1980)
        if criteria.year_to:
            update["year_to"] = criteria.year_to + 3
    elif step_name == "price_max":
        if not criteria.price_max:
            return criteria
        update["price_max"] = Decimal(str(criteria.price_max)) * Decimal("1.2")
    if not update:
        return criteria
    return criteria.model_copy(update=update)
