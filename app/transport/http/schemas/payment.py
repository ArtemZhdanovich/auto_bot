"""Схемы подписок и платежей (этап 6, ТЗ п. 4.3, 8.7, 9.6)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SubscriptionOut(BaseModel):
    """GET /api/subscription — статус и лимиты текущего тарифа."""

    tier: str
    paid_tier: str  # что куплено (может быть истёкшим)
    subscription_until: datetime | None
    max_filters: int | None  # None = без лимита
    ai_daily_limit: int | None
    instant: bool
    prices: dict[str, int]  # {"pro_month": 500, "pro_year": 4800, "business_month": 2500, ...}


class StarsInvoiceRequest(BaseModel):
    """POST /api/payments/stars — запрос инвойса Telegram Stars."""

    tier: Literal["pro", "business"]
    period: Literal["month", "year"] = "month"


class StarsInvoiceOut(BaseModel):
    payment_id: int
    invoice_url: str
    amount: int  # в Stars
    currency: str = "XTR"
    tier: str
    period_days: int


class PaymentPrepareIn(BaseModel):
    """POST /api/payments/prepare (internal, бот) — создать pending-платёж."""

    user_id: int
    tier: Literal["pro", "business"]
    period_days: int = Field(ge=1, le=400)


class PaymentPrepareOut(BaseModel):
    payment_id: int
    amount: int
    currency: str = "XTR"
    tier: str
    period_days: int
    title: str
    description: str


class PaymentSuccessIn(BaseModel):
    """POST /api/payments/success (internal, бот) — successful_payment."""

    payment_id: int
    user_id: int
    provider_payment_id: str
    amount: int


class UserSubscriptionOut(BaseModel):
    """GET /api/users/{id}/subscription (internal, бот) — для /subscribe."""

    user_id: int
    tier: str
    subscription_until: datetime | None
