"""DTO выдачи поиска: то, что сценарий отдаёт наружу (без ORM)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ListingPreview:
    """Короткая карточка объявления для превью агента."""

    id: int
    source: str
    url: str
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: Decimal | None = None
    currency: str | None = None
    mileage_km: int | None = None


@dataclass(frozen=True, slots=True)
class PreviewOutcome:
    """Результат превью: объявления и ослабленные параметры по-русски."""

    items: list[ListingPreview] = field(default_factory=list)
    relaxed: tuple[str, ...] = ()
