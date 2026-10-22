"""Сценарии партнёрского контура: claim одного/пачки лидов и пополнение.

Слой не знает про SQLAlchemy и FastAPI: порт хранилища, Unit of Work и
чистые правила из `app.domain.partners.pricing`.

Порядок шагов сохранён исходным сервисом:
1. проверка квоты, затем баланса (при пустом балансе — suspend + алерт);
2. атомарный claim лида/лидов;
3. списание и запись журнала в той же транзакции, `balance_after` из RETURNING.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.application.partners.dto import (
    ClaimResult,
    PartnerView,
    TxView,
)
from app.application.errors import NotFound
from app.application.partners.ports import PartnerRepository
from app.application.shared.ports import AuditLog, EventTracker, UnitOfWork
from app.domain.analytics.events import EVENT_LEAD_SOLD
from app.domain.partners import pricing
from app.domain.partners.ports import CLAIM_OK, PartnerAccountState, PartnerLead

CHARGE_LEAD = "charge_lead"


def _view(account: PartnerAccountState) -> PartnerView:
    """Снимок счёта → DTO с посчитанным CPL."""
    return PartnerView(
        id=account.id,
        name=account.name,
        plan=account.plan,
        status=account.status,
        balance=account.balance,
        daily_quota=account.daily_quota,
        cpl_cost=pricing.cpl_cost(account.plan, account.cpl_rate),
    )


async def _refusal(
    partners: PartnerRepository,
    account: PartnerAccountState,
    unit_cost: Decimal,
    n: int,
) -> str | None:
    """Причина отказа или `None`; при пустом балансе — suspend + алерт."""
    used = (
        await partners.claimed_today(account.id)
        if account.daily_quota is not None
        else 0
    )
    reason = pricing.can_claim(
        account.balance, unit_cost, account.daily_quota, used, n
    )
    if reason == pricing.INSUFFICIENT_BALANCE:
        await partners.suspend_for_empty_balance(account)
    return reason


class ClaimLead:
    """Забрать один лид: проверки, атомарный claim, списание."""

    def __init__(
        self,
        uow: UnitOfWork,
        partners: PartnerRepository,
        events: EventTracker | None = None,
    ) -> None:
        self._uow = uow
        self._partners = partners
        self._events = events

    async def _track(
        self, lead: PartnerLead | None, partner_id: int, plan: str
    ) -> None:
        """Событие `lead_sold`; сбой порта не должен ломать claim."""
        if self._events is None or lead is None:
            return
        await self._events(
            lead.user_id,
            EVENT_LEAD_SOLD,
            {"lead_id": lead.id, "partner_id": partner_id, "plan": plan},
        )

    async def execute(self, partner_id: int, lead_id: int) -> ClaimResult:
        """Выполнить claim.

        Args:
            partner_id: Партнёр, от имени которого идёт claim.
            lead_id: Лид из пула.

        Returns:
            Результат с причиной; `ok` и `already_yours` содержат id лида.

        Raises:
            NotFound: Партнёра нет.
        """
        async with self._uow:
            account = await self._partners.get_account(partner_id)
            if account is None:
                raise NotFound("Partner not found")
            unit_cost = pricing.cpl_cost(account.plan, account.cpl_rate)
            reason = await _refusal(self._partners, account, unit_cost, 1)
            if reason is not None:
                return ClaimResult(reason=reason)

            attempt = await self._partners.claim_lead_row(
                partner_id, lead_id, claimed_at=datetime.now(UTC)
            )
            if attempt.outcome != CLAIM_OK:
                lead_ids = (
                    (attempt.lead.id,) if attempt.lead is not None else ()
                )
                await self._track(attempt.lead, partner_id, account.plan)
                return ClaimResult(reason=attempt.outcome, lead_ids=lead_ids)

            balance_after = await self._partners.update_balance_returning(
                partner_id, -unit_cost
            )
            await self._partners.create_transaction(
                partner_id=partner_id,
                lead_id=attempt.lead.id if attempt.lead is not None else lead_id,
                amount=-unit_cost,
                kind=CHARGE_LEAD,
                balance_after=balance_after,
            )
            await self._uow.commit()
            await self._track(attempt.lead, partner_id, account.plan)
            lead_ids = (attempt.lead.id,) if attempt.lead is not None else ()
            return ClaimResult(
                reason=CLAIM_OK,
                lead_ids=lead_ids,
                cost=unit_cost,
                balance_after=balance_after,
            )


class ClaimBatch:
    """Забрать до `limit` свободных лидов одним пакетом."""

    def __init__(
        self,
        uow: UnitOfWork,
        partners: PartnerRepository,
        events: EventTracker | None = None,
    ) -> None:
        self._uow = uow
        self._partners = partners
        self._events = events

    async def _track(
        self, leads: tuple[PartnerLead, ...], partner_id: int, plan: str
    ) -> None:
        """Событие `lead_sold` на каждый забранный лид (`batch=True`)."""
        if self._events is None:
            return
        for lead in leads:
            await self._events(
                lead.user_id,
                EVENT_LEAD_SOLD,
                {
                    "lead_id": lead.id,
                    "partner_id": partner_id,
                    "plan": plan,
                    "batch": True,
                },
            )

    async def execute(self, partner_id: int, limit: int) -> ClaimResult:
        """Выполнить batch-claim.

        Квота и баланс проверяются на весь запрошенный объём: недобор не
        должен исполняться «частично».

        Raises:
            NotFound: Партнёра нет.
        """
        async with self._uow:
            account = await self._partners.get_account(partner_id)
            if account is None:
                raise NotFound("Partner not found")
            unit_cost = pricing.cpl_cost(account.plan, account.cpl_rate)
            reason = await _refusal(self._partners, account, unit_cost, limit)
            if reason is not None:
                return ClaimResult(reason=reason)

            leads = await self._partners.claim_batch_rows(
                partner_id, limit, claimed_at=datetime.now(UTC)
            )
            if not leads:
                return ClaimResult(reason=CLAIM_OK)  # пустой пул — не ошибка

            total = unit_cost * len(leads)
            balance_after = await self._partners.update_balance_returning(
                partner_id, -total
            )
            for lead in leads:
                await self._partners.create_transaction(
                    partner_id=partner_id,
                    lead_id=lead.id,
                    amount=-unit_cost,
                    kind=CHARGE_LEAD,
                    balance_after=balance_after,
                )
            await self._uow.commit()
            await self._track(leads, partner_id, account.plan)
            return ClaimResult(
                reason=CLAIM_OK,
                lead_ids=tuple(lead.id for lead in leads),
                cost=total,
                balance_after=balance_after,
            )


class TopUp:
    """Пополнить/скорректировать баланс и записать движение в журнал."""

    def __init__(
        self,
        uow: UnitOfWork,
        partners: PartnerRepository,
        audit: AuditLog | None = None,
    ) -> None:
        self._uow = uow
        self._partners = partners
        self._audit = audit

    async def execute(
        self,
        partner_id: int,
        amount: Decimal,
        *,
        comment: str | None = None,
        kind: str = "topup",
    ) -> tuple[PartnerView, TxView]:
        """Изменить баланс на `amount`.

        Args:
            partner_id: Партнёр.
            amount: `Decimal`, != 0.
            comment: Комментарий к транзакции.
            kind: `"topup"` / `"adjustment"`.

        Returns:
            Обновлённый счёт и запись журнала.

        Raises:
            ValueError: Нулевая сумма.
            NotFound: Партнёра нет.
        """
        if amount == 0:
            raise ValueError("amount must be non-zero")

        async with self._uow:
            account = await self._partners.get_account(partner_id)
            if account is None:
                raise NotFound("Partner not found")
            # Авто-разбан: только пополнение, выводящее баланс в плюс.
            reactivate = pricing.should_reactivate(
                kind, account.balance + amount, account.status
            )
            balance_after = await self._partners.update_balance_returning(
                partner_id, amount, status="active" if reactivate else None
            )
            await self._partners.create_transaction(
                partner_id=partner_id,
                lead_id=None,
                amount=amount,
                kind=kind,
                balance_after=balance_after,
                comment=comment,
            )
            await self._uow.commit()

        if self._audit is not None:
            # Audit — security-событие, но best-effort: деньги уже зачислены.
            await self._audit(
                action=f"partner.{kind}",
                entity="partner",
                entity_id=str(partner_id),
                payload={"amount": str(amount), "balance": str(balance_after)},
            )

        updated = replace(
            account,
            balance=balance_after,
            status="active" if reactivate else account.status,
        )
        tx = TxView(
            partner_id=partner_id,
            amount=amount,
            kind=kind,
            balance_after=balance_after,
            comment=comment,
        )
        return _view(updated), tx


__all__ = ["ClaimBatch", "ClaimLead", "TopUp"]
