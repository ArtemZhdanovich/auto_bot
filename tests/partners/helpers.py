"""Тестовые хелперы партнёрского контура: репозиторий и юзкейсы напрямую.

Бывшие инфраструктурные фасады (`services/partners/facades.py`) удалены:
сборка графа (UoW + репозиторий + юзкейс) — здесь, в тестах.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.partners.ports import PartnerRepository
from app.application.partners.dto import PartnerLeadView
from app.application.partners.queries import SetLeadFeedback
from app.application.partners.use_cases import ClaimBatch, ClaimLead, TopUp
from app.domain.partners.pricing import PLAN_CATALOG
from app.domain.partners.pricing import cpl_cost as _plan_cpl_cost
from app.infrastructure.db.models import Lead, PartnerAccount
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.infrastructure.services.partners import SqlAlchemyPartnerRepository


def _repo(session: AsyncSession) -> PartnerRepository:
    return SqlAlchemyPartnerRepository(session)


def _use_case(
    session: AsyncSession,
) -> tuple[SqlAlchemyUnitOfWork, SqlAlchemyPartnerRepository]:
    return SqlAlchemyUnitOfWork(session), _repo(session)


async def create_partner(
    session: AsyncSession,
    *,
    name: str,
    contact: str | None,
    plan: str,
    daily_quota: int,
    balance: Decimal,
    cpl_rate: Decimal | None,
) -> tuple[PartnerAccount, str]:
    if plan not in PLAN_CATALOG:
        raise ValueError(f"Unknown plan: {plan}")
    issued = await _repo(session).create_account(
        name=name,
        contact=contact,
        plan=plan,
        daily_quota=daily_quota,
        balance=balance,
        cpl_rate=cpl_rate,
    )
    account = await session.get(PartnerAccount, issued.partner_id)
    return account, issued.raw_key


async def rotate_key(session: AsyncSession, account: PartnerAccount) -> str:
    issued = await _repo(session).rotate_key(account.id)
    return issued.raw_key


async def revoke_keys(session: AsyncSession, account: PartnerAccount) -> None:
    await _repo(session).revoke_keys(account.id)


def cpl_cost(account) -> Decimal:
    """CPL по тарифу/ставке объекта с полями `plan`/`cpl_rate`."""
    return _plan_cpl_cost(account.plan, account.cpl_rate)


async def claimed_today(session: AsyncSession, partner_id: int) -> int:
    return await _repo(session).claimed_today(partner_id)


async def topup(
    session: AsyncSession,
    account: PartnerAccount,
    amount: Decimal,
    *,
    comment: str | None,
    kind: str,
) -> PartnerAccount:
    uow, repo = _use_case(session)
    await TopUp(uow, repo).execute(account.id, amount, comment=comment, kind=kind)
    await session.refresh(account)
    return account


async def available_leads(
    session: AsyncSession, *, limit: int, offset: int
) -> list:
    return await _repo(session).available_leads(limit=limit, offset=offset)


async def my_leads(
    session: AsyncSession,
    partner_id: int,
    *,
    limit: int,
    offset: int,
) -> list:
    return await _repo(session).my_leads(partner_id, limit=limit, offset=offset)


async def claim_lead(
    session: AsyncSession, account: PartnerAccount, lead_id: int
) -> tuple[Lead | None, str]:
    uow, repo = _use_case(session)
    result = await ClaimLead(uow, repo).execute(account.id, lead_id)

    if result.balance_after is not None:
        account.balance = result.balance_after
    elif result.reason == "insufficient_balance":
        await session.refresh(account)

    lead: Lead | None = None
    if result.lead_ids:
        lead = (
            await session.execute(
                select(Lead)
                .where(Lead.id == result.lead_ids[0])
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    return lead, result.reason


async def claim_batch(
    session: AsyncSession, account: PartnerAccount, limit: int
) -> tuple[list[Lead], str]:
    uow, repo = _use_case(session)
    result = await ClaimBatch(uow, repo).execute(account.id, limit)

    if result.balance_after is not None:
        account.balance = result.balance_after
    elif result.reason == "insufficient_balance":
        await session.refresh(account)

    if not result.lead_ids:
        return [], result.reason
    rows = (
        await session.execute(
            select(Lead)
            .where(Lead.id.in_(result.lead_ids))
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    leads = [by_id[lead_id] for lead_id in result.lead_ids if lead_id in by_id]
    return leads, result.reason


async def set_feedback(
    session: AsyncSession,
    account: PartnerAccount,
    lead_id: int,
    status: str,
) -> PartnerLeadView | None:
    """Обёртка над use case `SetLeadFeedback` (событие фидбека там)."""
    return await SetLeadFeedback(_repo(session)).execute(account.id, lead_id, status)
