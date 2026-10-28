"""Порт партнёрского хранилища: только то, что нужно сценариям claim/topup.

Записи — простые dataclass'ы, без ORM: прикладной слой не зависит от
SQLAlchemy, а реализация порта живёт в инфраструктуре.

Требования к реализации:
- `update_balance_returning` — один атомарный `UPDATE ... RETURNING balance`;
- `claim_lead_row` — один атомарный `UPDATE ... WHERE partner_id IS NULL
  ... RETURNING`, без read-modify-write;
- `claim_batch_rows` — `SELECT ... FOR UPDATE SKIP LOCKED` + один
  `UPDATE ... RETURNING`;
- `balance_after` транзакции всегда берётся из `RETURNING`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

CLAIM_OK = "ok"
CLAIM_ALREADY_YOURS = "already_yours"
CLAIM_TAKEN = "taken"
CLAIM_NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class PartnerAccountState:
    """Снимок партнёрского счёта для расчётов."""

    id: int
    name: str
    plan: str
    status: str
    balance: Decimal
    daily_quota: int | None = None
    cpl_rate: Decimal | None = None
    # Пакеты размещений рынка (миграция 0027): куплено/занято мест.
    placement_slots: int = 0
    placement_slots_used: int = 0


@dataclass(frozen=True, slots=True)
class PartnerLead:
    """Лид в партнёрском контуре (без ORM)."""

    id: int
    user_id: int | None = None
    partner_id: int | None = None
    partner_status: str | None = None
    qualification: dict[str, Any] | None = None
    claimed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClaimAttempt:
    """Итог одной атомарной попытки claim.

    `lead` заполнен для `ok` (забранный лид) и `already_yours` (лид партнёра,
    повторно не списывается); для `taken` / `not_found` — `None`.
    """

    outcome: str
    lead: PartnerLead | None = None
