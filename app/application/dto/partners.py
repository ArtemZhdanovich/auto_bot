"""DTO партнёрского контура (dataclass, не Pydantic).

Транспорт получает готовые значения и не считает деньги сам.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


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
