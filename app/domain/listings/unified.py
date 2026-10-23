"""Единый контракт объявления из источника (домен, без ORM/HTTP)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


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

    # Комплектация: русифицированные названия опций источника (Encar detail
    # `options.standard/choice/tuning`). Пишется writer'ом в `listings.options`.
    options: list[str] = Field(default_factory=list)

    # Отчёт осмотра источника (если отдаётся): словарь в формате отчёта
    # осмотра {source, grade, summary, marks, items, ...}. Пишется
    # writer'ом в `inspection_reports`; `None` — отчёта нет.
    inspection: Optional[dict[str, Any]] = None

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


