"""Тесты биллинга партнёров (P2-14): CPL-списания, topup, приостановка.

claim_lead/claim_batch списывают cpl_rate (или тариф плана) с баланса,
недостаток средств → insufficient_balance + suspended + алерт админу,
пополнение снимает приостановку. Миграция 0014: balance, cpl_rate,
partner_transactions.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from app.db.models import Lead, PartnerAccount
from app.services import partners
from tests.conftest import FakeSession


def _account(**kw: Any) -> PartnerAccount:
    defaults: dict[str, Any] = {
        "id": 1,
        "name": "Тест Партнёр",
        "plan": "starter",
        "daily_quota": 10,
        "status": "active",
        "balance": 50.0,
        "cpl_rate": None,
    }
    defaults.update(kw)
    return PartnerAccount(**defaults)


def _lead(**kw: Any) -> Lead:
    defaults: dict[str, Any] = {
        "id": 10,
        "user_id": 100,
        "status": "new",
        "partner_id": None,
        "qualification": {"budget": "high"},
    }
    defaults.update(kw)
    return Lead(**defaults)


@pytest.fixture(autouse=True)
def _no_alert(monkeypatch):
    async def _silent(*a: Any, **kw: Any) -> bool:
        return False

    monkeypatch.setattr(partners, "send_alert", _silent)


# ---------- cpl_cost ----------


def test_cpl_cost_plan_default():
    assert partners.cpl_cost(_account(plan="starter")) == 5.0
    assert partners.cpl_cost(_account(plan="pro")) == 3.0
    assert partners.cpl_cost(_account(plan="salon")) == 2.0


def test_cpl_cost_individual_rate_overrides_plan():
    assert partners.cpl_cost(_account(cpl_rate=7.5)) == 7.5


def test_cpl_cost_unknown_plan_falls_back_to_starter(monkeypatch):
    monkeypatch.setattr(
        partners,
        "get_settings",
        lambda: SimpleNamespace(partner_cpl_rates={"starter": 4.0}),
    )
    assert partners.cpl_cost(_account(plan="pro")) == 4.0


# ---------- claim_lead: списание ----------


async def test_claim_lead_charges_balance_and_writes_tx():
    session = FakeSession()
    session.add(_lead())
    account = _account(balance=50.0, plan="pro")  # cost 3.0

    got, reason = await partners.claim_lead(session, account, 10)

    assert reason == "ok" and got is not None
    assert account.balance == 47.0
    txs = [o for o in session.added if o.__class__.__name__ == "PartnerTransaction"]
    assert len(txs) == 1
    assert txs[0].amount == -3.0 and txs[0].kind == "charge_lead"
    assert txs[0].balance_after == 47.0 and txs[0].lead_id == 10


async def test_claim_lead_insufficient_balance_suspends():
    session = FakeSession()
    lead = _lead()
    session.add(lead)
    account = _account(balance=1.0, plan="starter")  # cost 5.0

    got, reason = await partners.claim_lead(session, account, 10)

    assert got is None and reason == "insufficient_balance"
    assert account.status == "suspended"
    assert lead.partner_id is None  # лид не тронут


async def test_claim_lead_suspension_alert_only_once(monkeypatch):
    session = FakeSession()
    session.add(_lead())
    account = _account(balance=0.0, plan="pro")
    alerts: list[str] = []

    async def _alert(text: str, **kw: Any) -> bool:
        alerts.append(text)
        return True

    monkeypatch.setattr(partners, "send_alert", _alert)

    await partners.claim_lead(session, account, 10)
    assert len(alerts) == 1
    await partners.claim_lead(session, account, 10)  # уже suspended
    assert len(alerts) == 1


async def test_claim_lead_quota_still_applies():
    session = FakeSession()
    session.add(_lead())
    account = _account(daily_quota=0)

    got, reason = await partners.claim_lead(session, account, 10)
    assert reason == "quota_exceeded" and got is None


# ---------- claim_batch: списание с учётом баланса ----------


async def test_claim_batch_charges_per_lead():
    session = FakeSession()
    leads = [_lead(id=i) for i in (11, 12, 13)]
    session.set_result(leads)
    account = _account(balance=20.0, plan="salon", daily_quota=10)  # cost 2.0

    got, reason = await partners.claim_batch(session, account, limit=3)

    assert reason == "ok" and len(got) == 3
    assert account.balance == 14.0  # 20 - 3 * 2.0
    txs = [o for o in session.added if o.__class__.__name__ == "PartnerTransaction"]
    assert len(txs) == 3 and all(t.kind == "charge_lead" for t in txs)


async def test_claim_batch_limited_by_balance():
    session = FakeSession()
    session.set_result([_lead(id=11), _lead(id=12)])
    account = _account(balance=5.0, plan="salon", daily_quota=10)  # хватает на 2

    got, reason = await partners.claim_batch(session, account, limit=3)

    assert reason == "ok" and len(got) == 2  # 5 // 2
    assert account.balance == 1.0


async def test_claim_batch_empty_balance_blocked():
    session = FakeSession()
    session.add(_lead(id=11))
    account = _account(balance=0.0, plan="salon", daily_quota=10)

    got, reason = await partners.claim_batch(session, account, limit=3)

    assert got == [] and reason == "insufficient_balance"
    assert account.status == "suspended"


async def test_claim_batch_quota_exceeded_still_first():
    session = FakeSession()
    account = _account(balance=100.0, daily_quota=0)

    got, reason = await partners.claim_batch(session, account, limit=3)
    assert reason == "quota_exceeded"


# ---------- topup / реактивация ----------


async def test_topup_increases_balance_and_reactivates():
    session = FakeSession()
    account = _account(balance=0.0, status="suspended")

    got = await partners.topup(session, account, 25.0, comment="инвойс #1")

    assert got.balance == 25.0 and got.status == "active"
    txs = [o for o in session.added if o.__class__.__name__ == "PartnerTransaction"]
    assert len(txs) == 1 and txs[0].amount == 25.0 and txs[0].kind == "topup"


async def test_topup_active_partner_stays_active():
    session = FakeSession()
    account = _account(balance=10.0, status="active")
    got = await partners.topup(session, account, 5.0)
    assert got.status == "active" and got.balance == 15.0


async def test_negative_adjustment_does_not_reactivate():
    session = FakeSession()
    account = _account(balance=10.0, status="suspended")
    got = await partners.topup(session, account, -4.0, kind="adjustment")
    assert got.balance == 6.0 and got.status == "suspended"