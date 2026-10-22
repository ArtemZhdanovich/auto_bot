"""Порты прикладного слоя партнёрского контура.

Интерфейс хранилища живёт здесь (прикладной слой), а не в домене.
Реализация — в пакете `infrastructure/services/partners` (SQL-репозиторий).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.domain.partners.ports import (
    ClaimAttempt,
    PartnerAccountState,
    PartnerLead,
)


class PartnerRepository(Protocol):
    """Хранилище партнёрского контура."""

    async def get_account(self, partner_id: int) -> PartnerAccountState | None:
        """Свежий снимок счёта или `None`, если партнёра нет."""

    async def update_balance_returning(
        self,
        partner_id: int,
        delta: Decimal,
        *,
        status: str | None = None,
    ) -> Decimal:
        """Атомарно изменить баланс и вернуть новое значение.

        `status` (если задан) пишется тем же `UPDATE`: реактивация после
        пополнения не должна требовать второго запроса.
        """

    async def create_transaction(
        self,
        *,
        partner_id: int,
        lead_id: int | None,
        amount: Decimal,
        kind: str,
        balance_after: Decimal,
        comment: str | None = None,
    ) -> None:
        """Записать движение по счёту в журнал (в текущей транзакции)."""

    async def claim_lead_row(
        self, partner_id: int, lead_id: int, *, claimed_at: datetime
    ) -> ClaimAttempt:
        """Атомарно забрать один лид; при промахе — объяснить причину."""

    async def claim_batch_rows(
        self, partner_id: int, limit: int, *, claimed_at: datetime
    ) -> tuple[PartnerLead, ...]:
        """Забрать до `limit` свободных лидов (`SKIP LOCKED`)."""

    async def available_leads(
        self, *, limit: int, offset: int
    ) -> list[PartnerLead]:
        """Пул: свободные лиды, старые первыми."""

    async def my_leads(
        self, partner_id: int, *, limit: int, offset: int
    ) -> list[PartnerLead]:
        """Лиды партнёра, свежие первыми."""

    async def claimed_today(self, partner_id: int) -> int:
        """Сколько лидов партнёр забрал с 00:00 UTC."""

    async def suspend_for_empty_balance(self, account: PartnerAccountState) -> None:
        """Приостановить партнёра из-за нулевого баланса и уведомить админа.

        Реализация коммитит изменение сама: вызывающий сразу возвращает
        отказ, а транспорт на отказе делает rollback — без commit статус
        `suspended` не сохранился бы.
        """
