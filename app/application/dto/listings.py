"""DTO объявления для прикладного слоя (без ORM и HTTP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ListingCard:
    """Карточка объявления, готовая к отдаче клиенту."""

    id: int
    source: str
    source_id: str
    url: str
    parsed_at: datetime
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: Decimal | None = None
    currency: str | None = None
    mileage_km: int | None = None
    vin: str | None = None
    body_type: str | None = None
    fuel: str | None = None
    transmission: str | None = None
    drive: str | None = None
    engine_volume: float | None = None
    color: str | None = None
    images: tuple[str, ...] = ()
    is_active: bool = True
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    updated_at: datetime | None = None
    price_changed_at: datetime | None = None
    title_raw: str | None = None
    title_ru: str | None = None
    source_language: str | None = None
    first_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PricePoint:
    """Точка истории цен объявления."""

    price: Decimal
    currency: str | None
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class FeedCursor:
    """Позиция keyset-пагинации ленты: (parsed_at, id) последнего элемента."""

    parsed_at: datetime
    listing_id: int


@dataclass(frozen=True, slots=True)
class ListingFeedQuery:
    """Разобранные параметры ленты объявлений."""

    make: str | None = None
    model: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    price_min: float | None = None
    price_max: float | None = None
    currency: str | None = None
    mileage_min: int | None = None
    mileage_max: int | None = None
    body_type: str | None = None
    fuel: str | None = None
    transmission: str | None = None
    drive: str | None = None
    engine_volume_min: float | None = None
    engine_volume_max: float | None = None
    color: str | None = None
    source: str | None = None
    cursor: str | None = None
    sort: str = "new"
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ListingPageResult:
    """Страница ленты в контракте `ListingListOut`."""

    total: int
    items: list[ListingCard]
    next_cursor: str | None = None
    has_more: bool = False


@dataclass(frozen=True, slots=True)
class ListingDetail:
    """Карточка объявления вместе с историей цен (PDP)."""

    card: ListingCard
    price_history: list[PricePoint]


@dataclass(frozen=True, slots=True)
class InspectionUpdate:
    """Данные для создания/замены отчёта осмотра."""

    source: str = "manual"
    inspector: str | None = None
    inspected_at: datetime | None = None
    grade: str | None = None
    summary: str | None = None
    marks: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class InspectionView:
    """Отчёт осмотра для PDP (без ORM)."""

    source: str
    inspector: str | None = None
    inspected_at: datetime | None = None
    grade: str | None = None
    summary: str | None = None
    marks: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
