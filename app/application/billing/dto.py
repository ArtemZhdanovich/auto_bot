"""DTO платежей и подписок (dataclass, не Pydantic).

Транспорт получает готовые значения (сумма в Stars, срок, лимиты тарифа)
и не считает деньги сам.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PaymentDraft:
    """Созданный pending-платёж: id, сумма в Stars, тариф и срок."""

    payment_id: int
    amount: int
    tier: str
    period_days: int


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    """Снимок платежа для активации подписки."""

    id: int
    user_id: int
    status: str | None
    tier: str | None
    period_days: int | None
    amount: Decimal


@dataclass(frozen=True, slots=True)
class SubscriptionState:
    """Подписка пользователя (`None` вместо объекта — пользователя нет).

    Имена полей совпадают с контрактом `app.domain.subscription.policies`,
    поэтому политики применяются к снимку без ORM.
    """

    subscription_tier: str | None
    subscription_until: datetime | None


@dataclass(frozen=True, slots=True)
class SubscriptionSnapshot:
    """Статус и лимиты действующего тарифа (Mini App)."""

    tier: str
    paid_tier: str
    subscription_until: datetime | None
    max_filters: int | None
    ai_daily_limit: int | None
    instant: bool
    prices: dict[str, int]


@dataclass(frozen=True, slots=True)
class UserSubscriptionView:
    """Статус тарифа пользователя (бот, `/subscribe`)."""

    user_id: int
    tier: str
    subscription_until: datetime | None


@dataclass(frozen=True, slots=True)
class PaymentConfirmation:
    """Итог `successful_payment`: подписка активна до `subscription_until`."""

    activated: bool
    tier: str | None
    subscription_until: datetime | None


# Валюта и провайдер Telegram Stars (контракт платёжного провайдера).
STARS_CURRENCY = "XTR"
STARS_PROVIDER = "stars"
