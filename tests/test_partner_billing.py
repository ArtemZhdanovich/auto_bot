"""Тесты биллинга партнёров (P2-14): CPL-списания, topup, документы.

Проверки идут на живой тестовой БД: списание/пополнение — атомарные
`UPDATE ... RETURNING`, на FakeSession их не проверить. Два сценария
помечены xfail: продукт больше не приостанавливает партнёра при недостатке
баланса (и не шлёт алерт), а `balance_after` в `charge_lead` пишется от уже
уменьшенного in-memory баланса — это дефекты продукта, не тестов.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from partners_helpers import (
    claim_batch,
    claim_lead,
    cpl_cost,
    create_partner,
    topup,
)

from app.infrastructure.db.models import Lead, PartnerAccount, PartnerTransaction, User
from app.domain.partners.pricing import can_claim


def _account(**kw) -> PartnerAccount:
    """Экземпляр без сохранения — только для чистого `cpl_cost`."""
    defaults: dict = {
        "name": "Тест Партнёр",
        "plan": "starter",
        "daily_quota": 10,
        "status": "active",
        "balance": Decimal("50.00"),
        "cpl_rate": None,
    }
    defaults.update(kw)
    return PartnerAccount(**defaults)


async def _partner(session, **kw) -> PartnerAccount:
    params: dict = {
        "name": "Тест Партнёр",
        "contact": None,
        "plan": "pro",
        "daily_quota": 10,
        "balance": Decimal("50.00"),
        "cpl_rate": None,
    }
    params.update(kw)
    account, _ = await create_partner(session, **params)
    return account


async def _lead_for_new_user(session, telegram_id: int = 930001) -> Lead:
    user = User(
        telegram_id=telegram_id,
        username=f"billing_{telegram_id}",
        role="buyer",
        subscription_tier="free",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    lead = Lead(user_id=telegram_id, status="new", qualification={"budget": "high"})
    session.add(lead)
    await session.flush()
    return lead


async def _balance(session, account: PartnerAccount) -> Decimal:
    return (
        await session.execute(
            select(PartnerAccount.balance).where(PartnerAccount.id == account.id)
        )
    ).scalar_one()


async def _txs(session) -> list[PartnerTransaction]:
    return list((await session.execute(select(PartnerTransaction))).scalars().all())


# ---------- cpl_cost ----------


def test_cpl_cost_plan_default():
    assert cpl_cost(_account(plan="starter")) == Decimal("5.00")
    assert cpl_cost(_account(plan="pro")) == Decimal("3.00")
    assert cpl_cost(_account(plan="salon")) == Decimal("2.00")


def test_cpl_cost_individual_rate_overrides_plan():
    assert cpl_cost(_account(cpl_rate=Decimal("7.50"))) == Decimal("7.50")


def test_cpl_cost_unknown_plan_falls_back_to_starter():
    # неизвестный план не должен ронять claim: fallback на starter
    assert cpl_cost(_account(plan="enterprise")) == Decimal("5.00")


def test_can_claim_checks_quota_before_balance():
    """Порядок причин отказа: исчерпанная квота важнее пустого баланса."""
    assert can_claim(Decimal("0.00"), Decimal("5.00"), 2, 2) == "quota_exceeded"
    assert can_claim(Decimal("100.00"), Decimal("5.00"), 2, 1) is None
    assert can_claim(Decimal("4.99"), Decimal("5.00"), None, 0) == "insufficient_balance"
    assert can_claim(Decimal("5.00"), Decimal("5.00"), None, 0) is None


def test_can_claim_batch_checks_whole_volume():
    """Квота и баланс считаются на весь batch, а не на один лид."""
    assert can_claim(Decimal("100.00"), Decimal("3.00"), 10, 8, 5) == "quota_exceeded"
    assert can_claim(Decimal("100.00"), Decimal("3.00"), 10, 8, 2) is None
    assert can_claim(Decimal("5.00"), Decimal("3.00"), 10, 0, 2) == "insufficient_balance"


# ---------- claim_lead: списание ----------


@pytest.mark.asyncio
async def test_claim_lead_charges_balance_and_writes_tx(session):
    account = await _partner(session, plan="pro", balance=Decimal("50.00"))  # CPL 3
    lead = await _lead_for_new_user(session)

    got, reason = await claim_lead(session, account, lead.id)

    assert reason == "ok" and got is not None
    assert await _balance(session, account) == Decimal("47.00")
    txs = await _txs(session)
    assert len(txs) == 1
    assert txs[0].amount == Decimal("-3.00") and txs[0].kind == "charge_lead"
    assert txs[0].lead_id == lead.id


@pytest.mark.asyncio
async def test_claim_lead_insufficient_balance_does_not_touch_lead(session):
    account = await _partner(session, plan="starter", balance=Decimal("1.00"))
    lead = await _lead_for_new_user(session)

    got, reason = await claim_lead(session, account, lead.id)

    assert got is None and reason == "insufficient_balance"
    await session.refresh(lead)
    assert lead.partner_id is None  # лид не тронут
    assert await _balance(session, account) == Decimal("1.00")
    assert await _txs(session) == []


@pytest.mark.asyncio
async def test_claim_lead_insufficient_balance_suspends(session):
    account = await _partner(session, plan="starter", balance=Decimal("1.00"))
    lead = await _lead_for_new_user(session)

    await claim_lead(session, account, lead.id)

    await session.refresh(account)
    assert account.status == "suspended"


@pytest.mark.asyncio
async def test_claim_lead_transaction_balance_after_matches_account(session):
    account = await _partner(session, plan="pro", balance=Decimal("50.00"))
    lead = await _lead_for_new_user(session)

    await claim_lead(session, account, lead.id)

    tx = (await _txs(session))[0]
    assert tx.balance_after == await _balance(session, account)


@pytest.mark.asyncio
async def test_claim_lead_quota_still_applies(session):
    account = await _partner(session, daily_quota=0)
    lead = await _lead_for_new_user(session)

    got, reason = await claim_lead(session, account, lead.id)
    assert reason == "quota_exceeded" and got is None


# ---------- claim_batch: списание ----------


@pytest.mark.asyncio
async def test_claim_batch_charges_per_lead(session):
    account = await _partner(
        session, plan="salon", daily_quota=10, balance=Decimal("20.00")
    )  # CPL 2
    leads = [await _lead_for_new_user(session, 940000 + i) for i in range(3)]

    got, reason = await claim_batch(session, account, limit=3)

    assert reason == "ok" and {lead.id for lead in got} == {lead.id for lead in leads}
    assert await _balance(session, account) == Decimal("14.00")  # 20 - 3 * 2
    txs = await _txs(session)
    assert len(txs) == 3 and all(tx.kind == "charge_lead" for tx in txs)
    assert all(tx.amount == Decimal("-2.00") for tx in txs)


@pytest.mark.asyncio
async def test_claim_batch_insufficient_balance_is_all_or_nothing(session):
    account = await _partner(
        session, plan="salon", daily_quota=10, balance=Decimal("3.00")
    )
    await _lead_for_new_user(session, 940001)
    await _lead_for_new_user(session, 940002)

    got, reason = await claim_batch(session, account, limit=3)

    assert got == [] and reason == "insufficient_balance"
    assert await _balance(session, account) == Decimal("3.00")


@pytest.mark.asyncio
async def test_claim_batch_quota_checked_before_balance(session):
    account = await _partner(session, balance=Decimal("0.00"), daily_quota=0)

    got, reason = await claim_batch(session, account, limit=3)
    assert got == [] and reason == "quota_exceeded"


# ---------- topup / реактивация ----------


@pytest.mark.asyncio
async def test_topup_increases_balance_and_reactivates(session):
    account = await _partner(session, balance=Decimal("0.00"))
    account.status = "suspended"
    await session.commit()

    got = await topup(
        session, account, Decimal("25.00"), comment="инвойс #1", kind="topup"
    )

    assert got.balance == Decimal("25.00") and got.status == "active"
    assert await _balance(session, account) == Decimal("25.00")
    txs = await _txs(session)
    assert len(txs) == 1
    assert txs[0].amount == Decimal("25.00") and txs[0].kind == "topup"
    assert txs[0].balance_after == Decimal("25.00")


@pytest.mark.asyncio
async def test_topup_active_partner_stays_active(session):
    account = await _partner(session, balance=Decimal("10.00"))

    got = await topup(
        session, account, Decimal("5.00"), comment=None, kind="topup"
    )

    assert got.status == "active" and got.balance == Decimal("15.00")


@pytest.mark.asyncio
async def test_negative_adjustment_does_not_reactivate(session):
    account = await _partner(session, balance=Decimal("10.00"))
    account.status = "suspended"
    await session.commit()

    got = await topup(
        session, account, Decimal("-4.00"), comment=None, kind="adjustment"
    )

    assert got.balance == Decimal("6.00") and got.status == "suspended"


@pytest.mark.asyncio
async def test_zero_amount_rejected(session):
    account = await _partner(session, balance=Decimal("10.00"))

    with pytest.raises(ValueError):
        await topup(
            session, account, Decimal("0"), comment=None, kind="adjustment"
        )
