"""UnifiedListing и FetchResult — единые контракты объявления (ТЗ п. 5.1, 5.3)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UnifiedListing(BaseModel):
    # Идентификация
    source: str  # "encar", "kcar", "mobile_de"
    source_id: str  # ID внутри источника
    url: HttpUrl
    schema_version: int = 1

    # Основное
    title: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    generation: Optional[str] = None
    year: Optional[int] = None

    # Цена
    price: Optional[Decimal] = None
    currency: Optional[str] = None  # "KRW", "EUR", "USD"

    # Характеристики
    mileage_km: Optional[int] = None
    vin: Optional[str] = None
    body_type: Optional[str] = None
    fuel: Optional[str] = None
    transmission: Optional[str] = None
    drive: Optional[str] = None
    engine_volume: Optional[float] = None
    color: Optional[str] = None

    # Продавец и локация
    location: Optional[str] = None
    seller_type: Optional[Literal["private", "dealer", "auction"]] = None
    condition: Optional[str] = None

    # Медиа
    images: list[HttpUrl] = Field(default_factory=list)

    # Время
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    parsed_at: datetime = Field(default_factory=_utcnow)

    # Сырые данные для отладки и перепарсинга
    raw: dict[str, Any] = Field(default_factory=dict)


class FetchResult(BaseModel):
    items: list[UnifiedListing]
    next_cursor: str | None = None
    has_more: bool = False
    source_meta: dict[str, Any] = Field(default_factory=dict)


class PricePointOut(BaseModel):
    """Точка истории цен (listing_price_history, PLAN.md фаза 4)."""

    price: float
    currency: Optional[str] = None
    recorded_at: datetime


class ListingOut(BaseModel):
    """Объявление для ленты/карточки Mini App (этап 5, ТЗ п. 9.2.4–9.2.5)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_id: str
    url: str
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    mileage_km: Optional[int] = None
    vin: Optional[str] = None
    images: list[Any] = Field(default_factory=list)
    parsed_at: datetime

    # Отслеживание (PLAN.md, фаза 4): is_active/first_seen_at приходят из ORM;
    # price_history дозаполняет только карточка (в ленте — N+1),
    # first_price — список избранного (бейдж «цена снизилась»).
    is_active: bool = True
    first_seen_at: Optional[datetime] = None
    first_price: Optional[float] = None
    price_history: Optional[list[PricePointOut]] = None

    # Перевод тайтла (PLAN.md, фаза 5): оригинал с источника + перевод (NLLB)
    title_raw: Optional[str] = None
    title_ru: Optional[str] = None


class ListingListOut(BaseModel):
    total: int
    items: list[ListingOut]
