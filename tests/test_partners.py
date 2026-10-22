"""Тесты партнёрского контура (P2-11): ключи, claim, фидбек, квоты, лимиты.

Сервисные инварианты проверяются на живой тестовой БД (fixture `session`):
`claim_lead`/`claim_batch` — атомарные `UPDATE ... RETURNING`/`SKIP LOCKED`,
на FakeSession их поведение не воспроизводится.

API-сценарии идут через ASGI-клиент с реальной сессией и FakeRedis.
Два сценария (`GET /api/partner/leads` в обоих режимах) помечены xfail:
схемы `PartnerLead*Out` объявлены без `from_attributes`, поэтому
`model_validate(Lead)` падает — это дефект продукта, не теста.
"""

import asyncio
import string
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.transport.http.deps.internal import internal_token
from app.infrastructure.security.partner_keys import (
    KEY_PREFIX,
    generate_api_key,
    hash_api_key,
    key_prefix,
)
from app.application.partners.dto import TxView
from app.application.partners.use_cases import ClaimLead
from app.domain.partners.ports import (
    CLAIM_OK,
    CLAIM_TAKEN,
    ClaimAttempt,
    PartnerAccountState,
    PartnerLead,
)
from app.infrastructure.db.models import (
    AnalyticsEvent,
    Lead,
    PartnerAccount,
    PartnerApiKey,
    PartnerTransaction,
    User,
)
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.infrastructure.services.partners import SqlAlchemyPartnerRepository
from app.main_api import app
from app.transport.http.schemas.partner import PartnerLeadOwnedOut, PartnerLeadPublicOut
from app.infrastructure.services import analytics
from conftest import FakeRedis, install_test_container
from partners_helpers import (
    available_leads,
    claim_batch,
    claim_lead,
    create_partner,
    my_leads,
    revoke_keys,
    rotate_key,
    set_feedback,
)

INTERNAL = {"X-Internal-Token": internal_token()}
# `KEY_PREFIX` + 32 base64url-символа (secrets.token_urlsafe(24)).
RAW_KEY_LEN = len(KEY_PREFIX) + 32


# --- Фикстуры и фабрики ------------------------------------------------------


@pytest.fixture
async def partner_client(session, fake_redis: FakeRedis):
    """ASGI-клиент: сессия теста + FakeRedis вместо настоящего Redis."""

    _prev = app.state.dishka_container
    _container = install_test_container(app, session=session, redis=fake_redis)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            yield http
    finally:
        app.state.dishka_container = _prev
        await _container.close()


async def make_user(session, telegram_id: int = 900100) -> User:
    user = User(
        telegram_id=telegram_id,
        username=f"partner_user_{telegram_id}",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


async def make_partner(session, **kw) -> tuple[PartnerAccount, str]:
    """Партнёр + первый ключ. По умолчанию: pro, квота 50, баланс 100."""
    params: dict = {
        "name": "Партнёр",
        "contact": None,
        "plan": "pro",
        "daily_quota": 50,
        "balance": Decimal("100.00"),
        "cpl_rate": None,
    }
    params.update(kw)
    return await create_partner(session, **params)


async def make_lead(session, user_id: int, **kw) -> Lead:
    params: dict = {
        "user_id": user_id,
        "status": "new",
        "partner_id": None,
        "partner_status": None,
        "qualification": {
            "budget_usd": 20000,
            "timeframe": "2 недели",
            "prepay": True,
        },
    }
    params.update(kw)
    lead = Lead(**params)
    session.add(lead)
    await session.flush()
    return lead


async def balance_of(session, account: PartnerAccount) -> Decimal:
    """Баланс из БД (а не из in-memory объекта)."""
    return (
        await session.execute(
            select(PartnerAccount.balance).where(PartnerAccount.id == account.id)
        )
    ).scalar_one()


async def transactions(session) -> list[PartnerTransaction]:
    return list(
        (await session.execute(select(PartnerTransaction))).scalars().all()
    )


# --- Ключи: генерация и хеширование -----------------------------------------


def test_new_raw_key_format_and_uniqueness():
    k1, k2 = generate_api_key(), generate_api_key()
    assert k1 != k2
    assert k1.startswith(KEY_PREFIX) and len(k1) == RAW_KEY_LEN
    # тело ключа — base64url (token_urlsafe), без посторонних символов
    assert set(k1[len(KEY_PREFIX):]) <= set(
        string.ascii_letters + string.digits + "-_"
    )


def test_hash_api_key_roundtrip():
    raw = generate_api_key()
    assert hash_api_key(raw) == hash_api_key(raw)
    assert hash_api_key(raw) != hash_api_key(generate_api_key())
    assert len(hash_api_key(raw)) == 64 and hash_api_key(raw) != raw
    assert key_prefix(raw) == raw[:10] + "…"


@pytest.mark.asyncio
async def test_create_partner_stores_hash_not_raw_key(session):
    account, raw = await make_partner(session, name="ИП Иванов", plan="pro")

    assert account.id is not None and account.status == "active"
    assert len(raw) == RAW_KEY_LEN and raw.startswith(KEY_PREFIX)

    keys = list(
        (
            await session.execute(
                select(PartnerApiKey).where(PartnerApiKey.partner_id == account.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(keys) == 1
    assert keys[0].key_hash == hash_api_key(raw) != raw
    assert keys[0].key_prefix == key_prefix(raw)
    assert keys[0].status == "active"


@pytest.mark.asyncio
async def test_create_partner_unknown_plan_rejected(session):
    with pytest.raises(ValueError):
        await make_partner(session, plan="enterprise")


@pytest.mark.asyncio
async def test_rotate_key_invalidates_old(session):
    account, old_raw = await make_partner(session)
    new_raw = await rotate_key(session, account)

    assert new_raw != old_raw
    old = (
        await session.execute(
            select(PartnerApiKey).where(
                PartnerApiKey.key_hash == hash_api_key(old_raw)
            )
        )
    ).scalar_one()
    assert old.status == "rotated" and old.rotated_at is not None

    active = list(
        (
            await session.execute(
                select(PartnerApiKey).where(
                    PartnerApiKey.partner_id == account.id,
                    PartnerApiKey.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(active) == 1
    assert active[0].key_hash == hash_api_key(new_raw)


@pytest.mark.asyncio
async def test_revoke_keys_revokes_all_idempotently(session):
    account, _ = await make_partner(session)
    await rotate_key(session, account)  # один rotated, один active

    await revoke_keys(session, account)
    statuses = set(
        (
            await session.execute(
                select(PartnerApiKey.status).where(
                    PartnerApiKey.partner_id == account.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert statuses == {"revoked"}

    await revoke_keys(session, account)  # повторный вызов не падает
    statuses = set(
        (
            await session.execute(
                select(PartnerApiKey.status).where(
                    PartnerApiKey.partner_id == account.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert statuses == {"revoked"}


# --- Claim: эксклюзивность, идемпотентность, квоты, деньги -------------------


@pytest.mark.asyncio
async def test_claim_lead_ok_sets_fields_and_charges(session):
    user = await make_user(session)
    account, _ = await make_partner(session, plan="pro")  # CPL 3.00
    lead = await make_lead(session, user.telegram_id)

    claimed, reason = await claim_lead(session, account, lead.id)

    assert reason == "ok" and claimed is not None
    assert claimed.partner_id == account.id
    assert claimed.partner_status == "claimed"
    assert claimed.claimed_at is not None
    assert await balance_of(session, account) == Decimal("97.00")

    txs = await transactions(session)
    assert len(txs) == 1
    assert txs[0].kind == "charge_lead"
    assert txs[0].amount == Decimal("-3.00")
    assert txs[0].lead_id == lead.id


@pytest.mark.asyncio
async def test_claim_exclusive_second_partner_gets_taken(session):
    user = await make_user(session)
    first, _ = await make_partner(session, name="Первый")
    second, _ = await make_partner(session, name="Второй")
    lead = await make_lead(session, user.telegram_id)

    await claim_lead(session, first, lead.id)
    claimed, reason = await claim_lead(session, second, lead.id)

    assert reason == "taken" and claimed is None
    await session.refresh(lead)
    assert lead.partner_id == first.id
    assert await balance_of(session, second) == Decimal("100.00")  # не списано


@pytest.mark.asyncio
async def test_claim_already_yours_is_idempotent(session):
    user = await make_user(session)
    account, _ = await make_partner(session)
    lead = await make_lead(session, user.telegram_id)

    first, _ = await claim_lead(session, account, lead.id)
    again, reason = await claim_lead(session, account, lead.id)

    assert reason == "already_yours" and again is not None
    assert again.id == first.id
    # повторный claim не списывает деньги второй раз
    assert await balance_of(session, account) == Decimal("97.00")
    assert len(await transactions(session)) == 1


@pytest.mark.asyncio
async def test_claim_quota_exceeded(session):
    user = await make_user(session)
    account, _ = await make_partner(session, daily_quota=2)
    leads = [await make_lead(session, user.telegram_id) for _ in range(3)]

    for lead in leads[:2]:
        _, reason = await claim_lead(session, account, lead.id)
        assert reason == "ok"

    claimed, reason = await claim_lead(session, account, leads[2].id)
    assert reason == "quota_exceeded" and claimed is None
    await session.refresh(leads[2])
    assert leads[2].partner_id is None  # лид остался в пуле
    assert await balance_of(session, account) == Decimal("94.00")  # 2 списания


@pytest.mark.asyncio
async def test_claim_insufficient_balance(session):
    user = await make_user(session)
    account, _ = await make_partner(session, plan="starter", balance=Decimal("1.00"))
    lead = await make_lead(session, user.telegram_id)

    claimed, reason = await claim_lead(session, account, lead.id)

    assert reason == "insufficient_balance" and claimed is None
    await session.refresh(lead)
    assert lead.partner_id is None
    assert await balance_of(session, account) == Decimal("1.00")
    assert await transactions(session) == []


@pytest.mark.asyncio
async def test_claim_missing_lead_not_found(session):
    account, _ = await make_partner(session)
    claimed, reason = await claim_lead(session, account, 999_999)
    assert reason == "not_found" and claimed is None


# --- Пул и «мои лиды» --------------------------------------------------------


@pytest.mark.asyncio
async def test_available_leads_returns_only_free_pool(session):
    user = await make_user(session)
    account, _ = await make_partner(session)
    free = [await make_lead(session, user.telegram_id) for _ in range(2)]
    claimed = await make_lead(
        session, user.telegram_id, partner_id=account.id, partner_status="claimed"
    )

    pool = await available_leads(session, limit=10, offset=0)
    ids = {lead.id for lead in pool}
    assert ids == {lead.id for lead in free}
    assert claimed.id not in ids


@pytest.mark.asyncio
async def test_my_leads_returns_only_own(session):
    user = await make_user(session)
    account, _ = await make_partner(session)
    other, _ = await make_partner(session, name="Другой")
    own = await make_lead(
        session, user.telegram_id, partner_id=account.id, partner_status="claimed"
    )
    await make_lead(session, user.telegram_id, partner_id=other.id)

    mine = await my_leads(session, account.id, limit=10, offset=0)
    assert [lead.id for lead in mine] == [own.id]


# --- Фидбек ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_only_own_lead(session):
    user = await make_user(session)
    account, _ = await make_partner(session)
    other, _ = await make_partner(session, name="Другой")
    foreign = await make_lead(
        session, user.telegram_id, partner_id=other.id, partner_status="claimed"
    )
    own = await make_lead(
        session, user.telegram_id, partner_id=account.id, partner_status="claimed"
    )

    assert await set_feedback(session, account, foreign.id, "deal") is None
    updated = await set_feedback(session, account, own.id, "deal")
    assert updated is not None and updated.partner_status == "deal"


# --- Batch-claim: атомарность и границы квоты/баланса -----------------------


@pytest.mark.asyncio
async def test_claim_batch_ok_charges_each_lead(session):
    user = await make_user(session)
    account, _ = await make_partner(
        session, plan="salon", daily_quota=10, balance=Decimal("20.00")
    )
    leads = [await make_lead(session, user.telegram_id) for _ in range(3)]

    claimed, reason = await claim_batch(session, account, limit=3)

    assert reason == "ok"
    assert {lead.id for lead in claimed} == {lead.id for lead in leads}
    assert all(lead.partner_id == account.id for lead in claimed)
    assert all(lead.partner_status == "claimed" for lead in claimed)
    assert await balance_of(session, account) == Decimal("14.00")  # 20 - 3 * 2

    txs = await transactions(session)
    assert len(txs) == 3
    assert {tx.lead_id for tx in txs} == {lead.id for lead in leads}
    assert all(tx.amount == Decimal("-2.00") for tx in txs)


@pytest.mark.asyncio
async def test_claim_batch_quota_is_all_or_nothing(session):
    """Квота проверяется на весь batch: недобор не «частично» исполняется."""
    user = await make_user(session)
    account, _ = await make_partner(session, daily_quota=2)
    await make_lead(session, user.telegram_id)

    claimed, reason = await claim_batch(session, account, limit=3)

    assert reason == "quota_exceeded" and claimed == []
    assert await balance_of(session, account) == Decimal("100.00")

    # ровно в остаток квоты — можно
    ok, reason = await claim_batch(session, account, limit=2)
    assert reason == "ok" and len(ok) == 1


@pytest.mark.asyncio
async def test_claim_batch_insufficient_balance_for_whole_batch(session):
    user = await make_user(session)
    account, _ = await make_partner(
        session, plan="salon", daily_quota=10, balance=Decimal("5.00")
    )
    await make_lead(session, user.telegram_id)
    await make_lead(session, user.telegram_id)

    claimed, reason = await claim_batch(session, account, limit=3)

    assert reason == "insufficient_balance" and claimed == []
    assert await balance_of(session, account) == Decimal("5.00")


@pytest.mark.asyncio
async def test_claim_batch_empty_pool_is_ok(session):
    account, _ = await make_partner(session, daily_quota=10)
    claimed, reason = await claim_batch(session, account, limit=3)
    assert reason == "ok" and claimed == []


# --- Use case партнёров на фейковом порте ----------------------------------


class FakeUnitOfWork:
    """UoW-заглушка: фиксирует commit/rollback."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakePartnerRepository:
    """Порт в памяти: один счёт, пул лидов, журнал движений."""

    def __init__(
        self,
        *,
        balance: Decimal = Decimal("100.00"),
        plan: str = "pro",
        quota: int | None = 50,
    ) -> None:
        self.account = PartnerAccountState(
            id=1,
            name="Тест",
            plan=plan,
            status="active",
            balance=balance,
            daily_quota=quota,
        )
        self.free: set[int] = {7, 8, 9}
        self.owned: set[int] = set()
        self.transactions: list[TxView] = []
        self.suspends = 0
        self.used = 0

    async def get_account(self, partner_id: int) -> PartnerAccountState | None:
        return self.account if partner_id == self.account.id else None

    async def update_balance_returning(
        self, partner_id: int, delta: Decimal, *, status: str | None = None
    ) -> Decimal:
        self.account = replace(
            self.account,
            balance=self.account.balance + delta,
            status=status or self.account.status,
        )
        return self.account.balance

    async def create_transaction(self, **fields) -> None:
        self.transactions.append(TxView(**fields))

    async def claim_lead_row(
        self, partner_id: int, lead_id: int, *, claimed_at: datetime
    ) -> ClaimAttempt:
        if lead_id in self.free:
            self.free.remove(lead_id)
            self.owned.add(lead_id)
            self.used += 1
            return ClaimAttempt(
                CLAIM_OK, PartnerLead(id=lead_id, partner_id=partner_id)
            )
        if lead_id in self.owned:
            return ClaimAttempt(
                "already_yours", PartnerLead(id=lead_id, partner_id=partner_id)
            )
        return ClaimAttempt(CLAIM_TAKEN)

    async def claimed_today(self, partner_id: int) -> int:
        return self.used

    async def suspend_for_empty_balance(
        self, account: PartnerAccountState
    ) -> None:
        self.suspends += 1
        self.account = replace(self.account, status="suspended")


@pytest.mark.asyncio
async def test_claim_lead_use_case_charges_through_port():
    repo = FakePartnerRepository(balance=Decimal("100.00"), plan="pro")
    uow = FakeUnitOfWork()

    result = await ClaimLead(uow, repo).execute(1, 7)

    assert result.reason == "ok" and result.lead_ids == (7,)
    assert result.balance_after == Decimal("97.00")
    assert repo.account.balance == Decimal("97.00")
    assert [tx.amount for tx in repo.transactions] == [Decimal("-3.00")]
    assert repo.transactions[0].balance_after == Decimal("97.00")
    assert uow.committed is True


@pytest.mark.asyncio
async def test_claim_lead_use_case_suspends_on_empty_balance():
    repo = FakePartnerRepository(balance=Decimal("1.00"), plan="starter")
    uow = FakeUnitOfWork()

    result = await ClaimLead(uow, repo).execute(1, 7)

    assert result.reason == "insufficient_balance" and result.lead_ids == ()
    assert repo.suspends == 1
    assert repo.transactions == [] and uow.committed is False
    assert 7 in repo.free  # лид не тронут


# --- API: /api/partner (X-API-Key) ------------------------------------------


async def _claimed_today_for(session, account: PartnerAccount) -> Lead:
    """Лид, захваченный партнёром сегодня (для квоты/статистики)."""
    user = await make_user(session, telegram_id=910000 + account.id)
    return await make_lead(
        session,
        user.telegram_id,
        partner_id=account.id,
        partner_status="claimed",
        claimed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "pi_unknown"}])
async def test_partner_api_requires_valid_key(partner_client, headers):
    resp = await partner_client.get("/api/partner/me", headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_partner_api_rejects_revoked_key(partner_client, session):
    account, raw = await make_partner(session)
    await revoke_keys(session, account)

    resp = await partner_client.get("/api/partner/me", headers={"X-API-Key": raw})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_partner_api_rejects_rotated_key(partner_client, session):
    account, old_raw = await make_partner(session)
    new_raw = await rotate_key(session, account)

    old_resp = await partner_client.get(
        "/api/partner/me", headers={"X-API-Key": old_raw}
    )
    new_resp = await partner_client.get(
        "/api/partner/me", headers={"X-API-Key": new_raw}
    )

    assert old_resp.status_code == 401  # ротация отключает старый ключ
    assert new_resp.status_code == 200


@pytest.mark.asyncio
async def test_partner_api_rejects_suspended_account(partner_client, session):
    account, raw = await make_partner(session)
    account.status = "suspended"
    await session.commit()

    resp = await partner_client.get("/api/partner/me", headers={"X-API-Key": raw})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_partner_me_quota_state(partner_client, session):
    account, raw = await make_partner(session, daily_quota=5)
    await _claimed_today_for(session, account)

    resp = await partner_client.get("/api/partner/me", headers={"X-API-Key": raw})

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "pro"
    assert body["claimed_today"] == 1
    assert body["quota_remaining"] == 4
    assert Decimal(body["balance"]) == Decimal("100.00")
    assert Decimal(body["cpl_cost"]) == Decimal("3.00")


def test_pool_schema_hides_pii_mine_exposes_it():
    """ПД-граница на уровне схем: в пуле нет `user_id`, в «моих» — есть."""
    assert "user_id" not in PartnerLeadPublicOut.model_fields
    assert "user_id" in PartnerLeadOwnedOut.model_fields


@pytest.mark.asyncio
async def test_partner_leads_pool_hides_user_id(partner_client, session):
    user = await make_user(session)
    account, raw = await make_partner(session)
    await make_lead(session, user.telegram_id)

    resp = await partner_client.get(
        "/api/partner/leads", headers={"X-API-Key": raw}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body and all("user_id" not in item for item in body)


@pytest.mark.asyncio
async def test_partner_leads_mine_shows_user_id(partner_client, session):
    user = await make_user(session)
    account, raw = await make_partner(session)
    lead = await make_lead(session, user.telegram_id)
    claimed, _ = await claim_lead(session, account, lead.id)
    assert claimed is not None

    resp = await partner_client.get(
        "/api/partner/leads?mine=true", headers={"X-API-Key": raw}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["user_id"] == user.telegram_id
    assert body[0]["partner_status"] == "claimed"


@pytest.mark.asyncio
async def test_claim_ok_returns_contact_and_event(partner_client, session):
    user = await make_user(session)
    account, raw = await make_partner(session)
    lead = await make_lead(session, user.telegram_id)

    resp = await partner_client.post(
        f"/api/partner/leads/{lead.id}/claim", headers={"X-API-Key": raw}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == user.telegram_id
    assert body["partner_status"] == "claimed"

    events = list(
        (
            await session.execute(
                select(AnalyticsEvent).where(
                    AnalyticsEvent.name == analytics.EVENT_LEAD_SOLD
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].props["lead_id"] == lead.id


@pytest.mark.asyncio
async def test_claim_conflict_409(partner_client, session):
    user = await make_user(session)
    other, _ = await make_partner(session, name="Другой")
    account, raw = await make_partner(session)
    lead = await make_lead(
        session, user.telegram_id, partner_id=other.id, partner_status="claimed"
    )

    resp = await partner_client.post(
        f"/api/partner/leads/{lead.id}/claim", headers={"X-API-Key": raw}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_claim_quota_429(partner_client, session):
    account, raw = await make_partner(session, daily_quota=1)
    await _claimed_today_for(session, account)
    user = await make_user(session, telegram_id=920001)
    lead = await make_lead(session, user.telegram_id)

    resp = await partner_client.post(
        f"/api/partner/leads/{lead.id}/claim", headers={"X-API-Key": raw}
    )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_claim_insufficient_balance_402(partner_client, session):
    account, raw = await make_partner(session, plan="pro", balance=Decimal("0.00"))
    user = await make_user(session)
    lead = await make_lead(session, user.telegram_id)

    resp = await partner_client.post(
        f"/api/partner/leads/{lead.id}/claim", headers={"X-API-Key": raw}
    )
    assert resp.status_code == 402


@pytest.mark.asyncio
async def test_feedback_requires_own_lead(partner_client, session):
    user = await make_user(session)
    other, _ = await make_partner(session, name="Другой")
    account, raw = await make_partner(session)
    foreign = await make_lead(
        session, user.telegram_id, partner_id=other.id, partner_status="claimed"
    )

    resp = await partner_client.post(
        f"/api/partner/leads/{foreign.id}/feedback",
        json={"status": "deal"},
        headers={"X-API-Key": raw},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_feedback_ok_and_event(partner_client, session):
    user = await make_user(session)
    account, raw = await make_partner(session)
    lead = await make_lead(
        session, user.telegram_id, partner_id=account.id, partner_status="claimed"
    )

    resp = await partner_client.post(
        f"/api/partner/leads/{lead.id}/feedback",
        json={"status": "junk"},
        headers={"X-API-Key": raw},
    )

    assert resp.status_code == 200
    assert resp.json()["partner_status"] == "junk"
    events = list(
        (
            await session.execute(
                select(AnalyticsEvent).where(
                    AnalyticsEvent.name == analytics.EVENT_LEAD_FEEDBACK
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1 and events[0].props["feedback"] == "junk"


@pytest.mark.asyncio
async def test_feedback_invalid_status_422(partner_client, session):
    account, raw = await make_partner(session)
    lead = await make_lead(session, (await make_user(session)).telegram_id)

    resp = await partner_client.post(
        f"/api/partner/leads/{lead.id}/feedback",
        json={"status": "bogus"},
        headers={"X-API-Key": raw},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_batch_claim_endpoint_ok(partner_client, session):
    user = await make_user(session)
    account, raw = await make_partner(session, plan="pro", daily_quota=10)
    leads = [await make_lead(session, user.telegram_id) for _ in range(2)]

    resp = await partner_client.post(
        "/api/partner/leads/batch",
        json={"limit": 2},
        headers={"X-API-Key": raw},
    )

    assert resp.status_code == 200
    claimed = resp.json()["claimed"]
    assert {item["lead_id"] for item in claimed} == {lead.id for lead in leads}

    events = list(
        (
            await session.execute(
                select(AnalyticsEvent).where(
                    AnalyticsEvent.name == analytics.EVENT_LEAD_SOLD
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 2 and all(event.props["batch"] is True for event in events)


@pytest.mark.asyncio
async def test_batch_claim_endpoint_quota_429(partner_client, session):
    account, raw = await make_partner(session, daily_quota=2)
    await _claimed_today_for(session, account)
    await make_lead(session, (await make_user(session, 920002)).telegram_id)

    resp = await partner_client.post(
        "/api/partner/leads/batch",
        json={"limit": 3},
        headers={"X-API-Key": raw},
    )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_batch_claim_endpoint_unauthorized(partner_client):
    resp = await partner_client.post("/api/partner/leads/batch", json={"limit": 2})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_billing_endpoint_catalog_and_usage(partner_client, session):
    account, raw = await make_partner(session, plan="pro", daily_quota=15)
    await _claimed_today_for(session, account)

    resp = await partner_client.get(
        "/api/partner/billing", headers={"X-API-Key": raw}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "pro"
    assert body["claimed_today"] == 1 and body["quota_remaining"] == 14
    assert {plan["key"] for plan in body["plans"]} == {"starter", "pro", "salon"}

    pro = next(plan for plan in body["plans"] if plan["key"] == "pro")
    assert pro["daily_quota"] == 50  # квота тарифа, не аккаунта
    assert Decimal(pro["cpl_cost"]) == Decimal("3.00")
    assert Decimal(body["balance"]) == Decimal("100.00")


@pytest.mark.asyncio
async def test_billing_endpoint_unauthorized(partner_client):
    resp = await partner_client.get("/api/partner/billing")
    assert resp.status_code == 401


# --- Admin API ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_create_partner_requires_internal_token(partner_client):
    resp = await partner_client.post("/api/admin/partners", json={"name": "ИП Иванов"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_create_partner_returns_raw_key_once(partner_client, session):
    resp = await partner_client.post(
        "/api/admin/partners",
        json={
            "name": "ИП Иванов",
            "contact": "@ivanov",
            "plan": "pro",
            "daily_quota": 50,
        },
        headers=INTERNAL,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["key"].startswith("pi_") and len(body["key"]) == RAW_KEY_LEN
    assert body["key_prefix"] == key_prefix(body["key"])

    stored = list(
        (
            await session.execute(
                select(PartnerApiKey).where(
                    PartnerApiKey.partner_id == body["partner_id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(stored) == 1
    assert stored[0].key_hash == hash_api_key(body["key"]) != body["key"]


# --- Rate limit --------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_fixed_window(fake_redis: FakeRedis):
    from app.config import get_settings
    from app.infrastructure.services.partners.rate_limit import RedisRateLimiter

    settings = get_settings()
    original = settings.partner_rate_limit_per_min
    settings.partner_rate_limit_per_min = 2
    try:
        limiter = RedisRateLimiter(fake_redis, settings)
        assert await limiter.check(1) is True
        assert await limiter.check(1) is True
        assert await limiter.check(1) is False  # лимит исчерпан
    finally:
        settings.partner_rate_limit_per_min = original


@pytest.mark.asyncio
async def test_rate_limit_fail_open():
    from app.config import get_settings
    from app.infrastructure.services.partners.rate_limit import RedisRateLimiter

    class BrokenRedis:
        async def incr(self, key: str) -> int:
            raise ConnectionError("redis down")

        async def expire(self, key: str, ttl: int) -> bool:
            return False

    limiter = RedisRateLimiter(BrokenRedis(), get_settings())
    assert await limiter.check(1) is True  # не бросает — fail-open

# --- IDOR / тенантность ------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_foreign_lead_404_and_untouched(partner_client, session):
    """Чужой лид: 404, статус в БД не меняется, событие не пишется."""
    user = await make_user(session, telegram_id=950001)
    owner, _ = await make_partner(session, name="Владелец")
    _, raw = await make_partner(session, name="Чужой")
    foreign = await make_lead(
        session,
        user.telegram_id,
        partner_id=owner.id,
        partner_status="claimed",
    )

    resp = await partner_client.post(
        f"/api/partner/leads/{foreign.id}/feedback",
        json={"status": "deal"},
        headers={"X-API-Key": raw},
    )

    assert resp.status_code == 404
    await session.refresh(foreign)
    assert foreign.partner_id == owner.id
    assert foreign.partner_status == "claimed"  # чужой лид не тронут
    events = list(
        (
            await session.execute(
                select(AnalyticsEvent).where(
                    AnalyticsEvent.name == analytics.EVENT_LEAD_FEEDBACK
                )
            )
        ).scalars().all()
    )
    assert events == []


@pytest.mark.asyncio
async def test_my_leads_excludes_other_partner(partner_client, session):
    """IDOR: `mine=true` отдаёт только лиды своего партнёра."""
    user = await make_user(session, telegram_id=950002)
    other, _ = await make_partner(session, name="Другой")
    account, raw = await make_partner(session, name="Свой")
    own = await make_lead(
        session, user.telegram_id, partner_id=account.id, partner_status="claimed"
    )
    await make_lead(
        session, user.telegram_id, partner_id=other.id, partner_status="claimed"
    )

    resp = await partner_client.get(
        "/api/partner/leads?mine=true", headers={"X-API-Key": raw}
    )

    assert resp.status_code == 200
    assert {item["id"] for item in resp.json()} == {own.id}


# --- Гонки: два одновременных claim одного лида -------------------------------


@pytest.mark.asyncio
async def test_parallel_claim_charges_exactly_once(engine):
    """Два одновременных claim одного лида → ровно одно списание.

    Отдельные соединения (не shared-session harness): оба сценария идут
    через реальный `UPDATE ... WHERE partner_id IS NULL RETURNING`.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    telegram_id = 999001
    account_id: int | None = None
    lead_id: int | None = None
    try:
        async with factory() as setup:
            setup.add(
                User(
                    telegram_id=telegram_id,
                    username="race_claim",
                    role="buyer",
                    subscription_tier="free",
                    is_active=True,
                )
            )
            account, _ = await create_partner(
                setup,
                name="Гонка",
                contact=None,
                plan="pro",
                daily_quota=50,
                balance=Decimal("100.00"),
                cpl_rate=None,
            )
            lead = Lead(user_id=telegram_id, status="new")
            setup.add(lead)
            await setup.commit()
            account_id, lead_id = account.id, lead.id

        async def claim() -> str:
            async with factory() as session:
                result = await ClaimLead(
                    SqlAlchemyUnitOfWork(session),
                    SqlAlchemyPartnerRepository(session),
                ).execute(account_id, lead_id)
                return result.reason

        reasons = await asyncio.gather(claim(), claim())

        # Выиграл ровно один. Проигравший в гонке same-partner claim
        # легитимно получает и `taken` (атомарный UPDATE не прошёл), и
        # `already_yours` (refusal увидел lead.partner_id == себя после
        # коммита победителя) — инвариант один: списание ровно одно.
        assert reasons.count("ok") == 1
        assert set(reasons) <= {"ok", "taken", "already_yours"}
        async with factory() as check:
            balance = (
                await check.execute(
                    select(PartnerAccount.balance).where(
                        PartnerAccount.id == account_id
                    )
                )
            ).scalar_one()
            tx_count = (
                await check.execute(
                    select(func.count())
                    .select_from(PartnerTransaction)
                    .where(PartnerTransaction.partner_id == account_id)
                )
            ).scalar_one()
            claimed_by = (
                await check.execute(
                    select(Lead.partner_id).where(Lead.id == lead_id)
                )
            ).scalar_one()
        assert balance == Decimal("97.00")  # 100 − 3 (CPL pro): одно списание
        assert tx_count == 1
        assert claimed_by == account_id
    finally:
        if account_id is not None and lead_id is not None:
            async with factory() as cleanup:
                await cleanup.execute(
                    delete(PartnerTransaction).where(
                        PartnerTransaction.partner_id == account_id
                    )
                )
                await cleanup.execute(
                    delete(PartnerApiKey).where(
                        PartnerApiKey.partner_id == account_id
                    )
                )
                await cleanup.execute(delete(Lead).where(Lead.id == lead_id))
                await cleanup.execute(
                    delete(PartnerAccount).where(
                        PartnerAccount.id == account_id
                    )
                )
                await cleanup.execute(
                    delete(User).where(User.telegram_id == telegram_id)
                )
                await cleanup.commit()
