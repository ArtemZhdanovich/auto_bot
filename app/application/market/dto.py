"""DTO витрины внутреннего рынка (§1: partner_offers + лоты).

Редизайн часть 6 добавляет лоты-с-владельцем (owner_type='user'|'partner')
и placement-модель: у юзера — бесплатный период / включённый лот платного
тарифа / продление за Stars; у партнёра — пакеты размещений (слоты).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MarketOfferView:
    """Лот витрины: карточка объявления + партнёрская заметка."""

    listing_id: int
    partner_id: int
    note: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: float | None = None
    currency: str | None = None
    mileage_km: int | None = None
    url: str | None = None
    image: str | None = None
    # Бейдж «Рынок» на карточке (лента фильтрует source=market).
    has_market: bool = True


# ---------------------------------------------------------------------------
# Лоты рынка с владельцем (§1-§3, миграция 0027)
# ---------------------------------------------------------------------------

# placement-статусы лота (§3): включённый (платный тариф), бесплатный
# период, оплачено до, срок истёк, ждёт оплаты, партнёрский слот.
PLACEMENT_INCLUDED = "included"
PLACEMENT_FREE = "free"
PLACEMENT_PAID = "paid"
PLACEMENT_EXPIRED = "expired"
PLACEMENT_NEEDS_PAYMENT = "needs_payment"
PLACEMENT_SLOT = "slot"
# Размещение ещё не решалось (10 §1): draft/moderation/rejected — лот
# не публиковался, placement-правила применяются на accept.
PLACEMENT_NONE = "none"

# Статусы жизненного цикла owner-лота (миграция 0030, 10 §1). Внешние
# лоты (encar/kcar/che168) живут с 'accepted' — они вне модерации.
LISTING_STATUS_DRAFT = "draft"
LISTING_STATUS_MODERATION = "moderation"
LISTING_STATUS_ACCEPTED = "accepted"
LISTING_STATUS_REJECTED = "rejected"
LISTING_STATUS_ARCHIVED = "archived"
LISTING_STATUS_EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class CreateLotCommand:
    """Ввод создания лота рынка (user и partner, §2).

    `country` (GEO-V1 §2) обязателен для owner-лотов — страна продавца из
    формы SellCar (ISO-3166 alpha-2, enum известных + свобода); `city`
    опционален и только для owner-лотов.
    """

    make: str
    model: str
    year: int
    price: Decimal
    currency: str  # 'KRW' | 'USD' | 'RUB'
    mileage_km: int | None = None
    fuel: str | None = None
    transmission: str | None = None
    description: str | None = None  # owner_description, <=2000
    photos: tuple[str, ...] = field(default_factory=tuple)
    country: str | None = None
    city: str | None = None


@dataclass(frozen=True, slots=True)
class NewMarketLot:
    """Полная команда вставки owner-лота (собирается сценарием по §3)."""

    owner_type: str  # 'user' | 'partner'
    owner_user_id: int | None = None
    owner_partner_id: int | None = None
    command: CreateLotCommand | None = None
    is_active: bool = True
    placement_free_until: datetime | None = None
    placement_paid_until: datetime | None = None
    # Статус жизненного цикла (10 §1): draft до submit, accepted у
    # партнёров (сэмплинг может перевести в moderation на экранинге).
    status: str = LISTING_STATUS_DRAFT


@dataclass(frozen=True, slots=True)
class MarketLotSnapshot:
    """Снимок owner-лота: правила публикации, my-lots, seller-чат (§3, §5)."""

    listing_id: int
    owner_type: str
    is_active: bool = True
    owner_user_id: int | None = None
    owner_partner_id: int | None = None
    owner_description: str | None = None
    placement_free_until: datetime | None = None
    placement_paid_until: datetime | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: Decimal | None = None
    currency: str | None = None
    mileage_km: int | None = None
    fuel: str | None = None
    transmission: str | None = None
    images: tuple[str, ...] = ()
    url: str | None = None
    created_at: datetime | None = None
    # Гео (GEO-V1 §2): страна/город owner-лота из формы SellCar.
    country: str | None = None
    city: str | None = None
    # Жизненный цикл (10 §1): статус, причина отказа, вердикт последнего
    # скрининга и версия-на-модерации (правка опубликованного лота).
    status: str = LISTING_STATUS_ACCEPTED
    rejected_reason: str | None = None
    moderation_payload: dict | None = None
    pending_patch: dict | None = None


@dataclass(frozen=True, slots=True)
class CreatedLotView:
    """Итог создания лота: id + placement-статус (201 с пометкой, §2)."""

    listing_id: int
    placement_status: str
    is_active: bool
    # Лот создан неактивным и ждёт оплаты (доп. лот после первого).
    needs_payment: bool = False
    # Статус жизненного цикла после создания/submit (10 §1): draft |
    # accepted | moderation (партнёр из сэмплинга).
    status: str = LISTING_STATUS_DRAFT
    # Качество объявления (12 §2): по полям формы создания.
    quality: dict | None = None


@dataclass(frozen=True, slots=True)
class SubmittedLotView:
    """Итог submit: новый статус лота + вердикт модерации (10 §4)."""

    listing_id: int
    status: str
    placement_status: str
    is_active: bool
    needs_payment: bool = False
    rejected_reason: str | None = None
    violations: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class MyLotView:
    """«Мои объявления»: карточка + placement-статус и дедлайны (§2)."""

    listing_id: int
    placement_status: str
    is_active: bool
    placement_free_until: datetime | None = None
    placement_paid_until: datetime | None = None
    # Дедлайн размещения = max(free, paid) (для included/slot — None).
    placement_deadline: datetime | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: Decimal | None = None
    currency: str | None = None
    mileage_km: int | None = None
    images: tuple[str, ...] = ()
    url: str | None = None
    owner_description: str | None = None
    # Гео (GEO-V1 §2): страна/город owner-лота (карточка «🇷🇺 RU · Москва»).
    country: str | None = None
    city: str | None = None
    # Жизненный цикл (10 §1): статус-пилюля, причина отказа, сработавшие
    # правила и патч на проверке (с patch_reject_reason при отказе).
    status: str = LISTING_STATUS_ACCEPTED
    rejected_reason: str | None = None
    violations: tuple[dict, ...] = ()
    pending_patch: dict | None = None
    # Качество объявления (12 §2): score 0-100 + советы my-lots/SellCar.
    quality: dict | None = None


@dataclass(frozen=True, slots=True)
class ModerationOutcome:
    """Итог решения администратора по лоту/патчу (10 §2)."""

    listing_id: int
    status: str
    placement_status: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class LotPatch:
    """Частичная правка лота (PATCH /api/market/lots/{id}, 10 §4).

    Заданы только переданные поля: draft/rejected обновляются напрямую,
    accepted складывается в `pending_patch` (версия-на-модерации).
    """

    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: Decimal | None = None
    currency: str | None = None
    mileage_km: int | None = None
    fuel: str | None = None
    transmission: str | None = None
    description: str | None = None
    photos: tuple[str, ...] | None = None
    country: str | None = None
    city: str | None = None

    def fields(self) -> dict:
        """Словарь заданных полей (photos → list) для репозитория/патча."""
        result = {
            key: value
            for key, value in dataclasses.asdict(self).items()
            if value is not None
        }
        if "photos" in result:
            result["photos"] = list(self.photos or ())
        return result

    def json_fields(self) -> dict:
        """`fields()` в JSON-безопасном виде (Decimal → str) — для
        записи в pending_patch JSONB и ответов API."""
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in self.fields().items()
        }


@dataclass(frozen=True, slots=True)
class PackageView:
    """Пакет размещений партнёра: размер и цена (₽) из конфига (§2).

    `slots_free` — свободные места после покупки (только в ответе POST;
    в каталоге GET — None): кабинет сразу показывает актуальный остаток.
    """

    size: int
    price_rub: Decimal
    slots_free: int | None = None


@dataclass(frozen=True, slots=True)
class PlacementInvoice:
    """Pending-платёж продления размещения (kind='placement', §2)."""

    payment_id: int
    listing_id: int
    weeks: int
    amount: int  # в Stars

