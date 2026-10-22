"""DTO партнёрского контура: счёт, ключи, лиды (dataclass, не Pydantic).

Транспорт получает готовые значения и не считает деньги сам. `LeadView` —
лид в объёме CRM (`/api/admin/leads`); `PartnerLeadView` — лид глазами
партнёра (пул и «мои»), поля различаются сознательно.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# Партнёры
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartnerView:
    """Состояние партнёрского счёта."""

    id: int
    name: str
    plan: str
    status: str
    balance: Decimal
    daily_quota: int | None
    cpl_cost: Decimal


@dataclass(frozen=True, slots=True)
class TxView:
    """Одно движение по счёту (для ответа/аудита)."""

    partner_id: int
    amount: Decimal
    kind: str
    balance_after: Decimal
    lead_id: int | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Итог claim'а: причина, забранные лиды и списание.

    `reason` использует коды сервиса: `ok`, `already_yours`, `taken`,
    `not_found`, `quota_exceeded`, `insufficient_balance`.
    """

    reason: str
    lead_ids: tuple[int, ...] = ()
    cost: Decimal = Decimal("0")
    balance_after: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PartnerLeadView:
    """Лид для партнёрского API (пул и «мои»)."""

    id: int
    source: str | None
    status: str
    comment: str | None
    qualification: dict[str, Any] | None
    created_at: datetime
    partner_id: int | None = None
    partner_status: str | None = None
    claimed_at: datetime | None = None
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class PartnerAdminView:
    """Счёт партнёра для admin-выдачи (с префиксом ключа и расходом квоты)."""

    id: int
    name: str
    contact: str | None
    plan: str
    daily_quota: int
    status: str
    balance: Decimal
    cpl_rate: Decimal | None
    created_at: datetime
    claimed_today: int = 0
    key_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class PartnerOverview:
    """Состояние счёта для ЛК партнёра (`/me` и `/billing`)."""

    id: int
    name: str
    plan: str
    daily_quota: int
    claimed_today: int
    quota_remaining: int
    balance: Decimal
    cpl_cost: Decimal
    key_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class PlanView:
    """Тариф каталога: квота по умолчанию и цена лида."""

    key: str
    title: str
    daily_quota: int
    cpl_cost: Decimal
    description: str | None = None


@dataclass(frozen=True, slots=True)
class BillingView:
    """Биллинг партнёра: счёт + каталог тарифов."""

    overview: PartnerOverview
    plans: tuple[PlanView, ...]


@dataclass(frozen=True, slots=True)
class IssuedKey:
    """Выданный API-ключ: raw показывается один раз, в БД — только хэш."""

    partner_id: int
    raw_key: str
    prefix: str


@dataclass(frozen=True, slots=True)
class PartnerAuthState:
    """Снимок успешной аутентификации по `X-API-Key`.

    Транспорт получает только идентификаторы — ORM-объекты ключа и
    аккаунта не покидают инфраструктуру.
    """

    key_id: int
    partner_id: int
    partner_name: str


# ---------------------------------------------------------------------------
# Лиды
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeadView:
    """Лид в объёме ответа API (без ORM-объекта)."""

    id: int
    user_id: int
    listing_id: int | None
    source: str | None
    status: str
    manager_id: int | None
    comment: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LeadPatch:
    """Частичное обновление лида: `None` означает «не менять»."""

    status: str | None = None
    manager_id: int | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class CreateLeadCommand:
    """Вход сценария создания лида."""

    user_id: int
    listing_id: int | None = None
    source: str | None = None
    comment: str | None = None
    idempotency_key: str | None = None
