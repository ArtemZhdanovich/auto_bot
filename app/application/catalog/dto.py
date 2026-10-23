"""DTO каталога: лента объявлений, фильтры, ингест, AI-анализ цены.

Сценарии отдают наружу ровно эти структуры — без ORM и HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.favorites.ports import FavoriteCursor
from app.domain.listings.ingestion import IngestStats, PendingRawUpload
from app.domain.search.criteria import SearchCriteria

__all__ = [
    "AnalysisCommand",
    "AnalyzePriceCommand",
    "CreateFilterCommand",
    "DeleteFilterCommand",
    "FeedCursor",
    "FilterDraft",
    "FilterPatch",
    "FilterRecord",
    "GUEST_FORBIDDEN_MESSAGE",
    "IngestStats",
    "InspectionUpdate",
    "InspectionView",
    "ListingCard",
    "ListingDetail",
    "ListingFeedQuery",
    "ListingPageResult",
    "ListingPreview",
    "ListFavoritesQuery",
    "ListFiltersQuery",
    "ListingSnapshot",
    "NotifySettingsCommand",
    "NotifySettingsResult",
    "NotifyTarget",
    "PendingRawUpload",
    "PreviewListingsQuery",
    "PreviewOutcome",
    "PriceAnalysisResult",
    "PricePoint",
    "QUOTA_EXCEEDED_MESSAGE",
    "ScanResult",
    "ToggleFavoriteCommand",
    "UpdateFilterCommand",
]


# ---------------------------------------------------------------------------
# Лента объявлений (dto/listings)
# ---------------------------------------------------------------------------


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
    images: list[str] = field(default_factory=list)


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
    images: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Фильтры (dto/filters + команды сценариев)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FilterRecord:
    """Сохранённый фильтр в контракте `FilterOut`."""

    id: int
    name: str | None
    criteria: dict
    notify_mode: str
    quiet_hours: str | None
    created_at: datetime
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class FilterDraft:
    """Данные нового фильтра."""

    name: str | None
    criteria: dict
    notify_mode: str = "digest"
    quiet_hours: str | None = None


@dataclass(frozen=True, slots=True)
class FilterPatch:
    """Частичное обновление: `None` — поле не меняется."""

    name: str | None = None
    criteria: dict | None = None
    notify_mode: str | None = None
    quiet_hours: str | None = None


@dataclass(frozen=True, slots=True)
class ListFiltersQuery:
    """Вход сценария списка фильтров."""

    user_id: int
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class CreateFilterCommand:
    """Вход сценария создания фильтра."""

    user: Any
    draft: FilterDraft


@dataclass(frozen=True, slots=True)
class UpdateFilterCommand:
    """Вход сценария обновления фильтра."""

    user: Any
    filter_id: int
    patch: FilterPatch


@dataclass(frozen=True, slots=True)
class DeleteFilterCommand:
    """Вход сценария удаления фильтра."""

    user: Any
    filter_id: int


# ---------------------------------------------------------------------------
# Избранное и уведомления (команды сценариев)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToggleFavoriteCommand:
    """Вход сценария toggle."""

    user_id: int
    listing_id: int


@dataclass(frozen=True, slots=True)
class ListFavoritesQuery:
    """Вход сценария списка избранного."""

    user_id: int
    limit: int = 50
    cursor: FavoriteCursor | None = None
    offset: int = 0


@dataclass(frozen=True, slots=True)
class NotifyTarget:
    """Минимум о пользователе для проверок настроек."""

    user_id: int
    is_active: bool
    subscription_tier: str
    subscription_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class NotifySettingsCommand:
    """Вход сценария настроек; `None` = поле не передано."""

    user_id: int
    notify_mode: str | None = None
    quiet_hours: str | None = None
    quiet_hours_clear: bool = False


@dataclass(frozen=True, slots=True)
class NotifySettingsResult:
    """Итог настроек: сколько фильтров обновлено и пометка no-op."""

    user_id: int
    updated: int
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Итог ручного скана."""

    job_id: str


# ---------------------------------------------------------------------------
# AI-анализ цены (dto/analysis + dto/pricing + команда сценария)
# ---------------------------------------------------------------------------

GUEST_FORBIDDEN_MESSAGE = (
    "guest_forbidden: AI-анализ цены доступен только после входа или регистрации"
)
QUOTA_EXCEEDED_MESSAGE = (
    "quota_exceeded: 1 разбор этой машины в неделю на Free. Безлимит — в Pro"
)


@dataclass(frozen=True, slots=True)
class AnalysisCommand:
    """Запрос разбора: кто, по какой машине и через какой канал."""

    user_id: int
    listing_id: int
    channel: str = "web"


@dataclass(frozen=True, slots=True)
class ListingSnapshot:
    """Поля объявления, нужные разбору (без ORM-объекта)."""

    id: int
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: float | None = None
    currency: str | None = None
    mileage_km: int | None = None


@dataclass(frozen=True, slots=True)
class PriceAnalysisResult:
    """Готовый разбор цены; `to_payload` — контракт ответа API."""

    verdict: str
    price: float
    currency: str
    summary: str
    fair_min: float | None = None
    fair_max: float | None = None
    factors: tuple[str, ...] = ()
    comps: dict[str, Any] | None = None
    history: tuple[dict[str, Any], ...] = ()
    source: str = "stats"
    created_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Собрать JSON-совместимый ответ (поля — контракт `AnalysisOut`)."""
        return {
            "verdict": self.verdict,
            "price": self.price,
            "currency": self.currency,
            "fair_min": self.fair_min,
            "fair_max": self.fair_max,
            "summary": self.summary,
            "factors": list(self.factors),
            "comps": self.comps,
            "history": list(self.history),
            "source": self.source,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class AnalyzePriceCommand:
    """Вход сценария: объявление, пользователь и остаток бесплатной квоты."""

    listing: ListingSnapshot
    user_id: int
    tier: str = "free"
    free_quota_left: int | None = None


# ---------------------------------------------------------------------------
# Превью поиска (dto/search + команда сценария)
# ---------------------------------------------------------------------------


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


@dataclass(frozen=True, slots=True)
class PreviewListingsQuery:
    """Вход сценария превью."""

    criteria: SearchCriteria
    limit: int = 3
