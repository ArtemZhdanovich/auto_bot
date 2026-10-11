"""Тесты партнёрского контура (P2-11): ключи, ротация, claim, фидбек, лимиты."""

import pytest
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.deps_internal import get_redis
from app.core.deps_partner import check_rate_limit
from app.db.models import AnalyticsEvent, Lead, PartnerAccount, PartnerApiKey
from app.db.session import get_session
from app.main import app
from app.services import partners
from conftest import FakeRedis, FakeSession

INTERNAL = {"X-Internal-Token": get_settings().app_secret_key}
RAW_KEY = "pi_" + "a" * 40


class SeqSession(FakeSession):
    """FakeSession с очередью результатов execute() (по одному на вызов)."""

    def __init__(self) -> None:
        super().__init__()
        self.queue: list[list] = []

    def set_queue(self, *rows_lists: list) -> None:
        # каждый аргумент — строки одного вызова execute()
        self.queue = [list(rows) for rows in rows_lists]

    async def execute(self, stmt):
        self.executed.append(stmt)
        rows = self.queue.pop(0) if self.queue else self._rows

        class _Result:
            def scalars(self) -> "_Result":
                return self

            def all(self) -> list:
                return list(rows)

            def scalar_one_or_none(self):
                return rows[0] if rows else None

            def scalar_one(self) -> int:
                return len(rows)

        return _Result()


def make_account(**kw) -> PartnerAccount:
    defaults = dict(name="Партнёр", plan="pro", daily_quota=5, status="active")
    defaults.update(kw)
    account = PartnerAccount(**defaults)
    account.id = 1
    return account


def make_key(status: str = "active", partner_id: int = 1) -> PartnerApiKey:
    return PartnerApiKey(
        partner_id=partner_id,
        key_hash=partners.hash_key(RAW_KEY),
        key_prefix=RAW_KEY[:10],
        status=status,
    )


def make_lead(lead_id: int, **kw) -> Lead:
    defaults = dict(
        created_at=datetime.now(UTC),
        id=lead_id,
        user_id=5,
        status="new",
        partner_id=None,
        partner_status=None,
        qualification={"budget_usd": 20000, "timeframe": "2 недели", "prepay": True},
    )
    defaults.update(kw)
    return Lead(**defaults)


# --- Сервис: ключи -----------------------------------------------------------


@pytest.mark.asyncio
async def test_key_generation_unique_and_hash_roundtrip():
    k1, k2 = partners.generate_api_key(), partners.generate_api_key()
    assert k1 != k2 and k1.startswith("pi_") and len(k1) == 43
    assert partners.hash_key(k1) == partners.hash_key(k1)
    assert partners.hash_key(k1) != partners.hash_key(k2)


@pytest.mark.asyncio
async def test_authenticate_key_active():
    s = FakeSession()
    key, account = make_key(), make_account()
    s.add(account)
    s.set_result([key])
    auth = await partners.authenticate_key(s, RAW_KEY)
    assert auth is not None
    assert auth[1].id == 1


@pytest.mark.asyncio
async def test_authenticate_rotated_or_revoked_key_rejected():
    for status in ("rotated", "revoked"):
        s = FakeSession()
        s.set_result([make_key(status=status)])
        assert await partners.authenticate_key(s, RAW_KEY) is None


@pytest.mark.asyncio
async def test_authenticate_unknown_key_rejected():
    s = FakeSession()
    s.set_result([])
    assert await partners.authenticate_key(s, "pi_b" + "0" * 37) is None


@pytest.mark.asyncio
async def test_authenticate_suspended_account_rejected():
    s = FakeSession()
    s.add(make_account(status="suspended"))
    s.set_result([make_key()])
    assert await partners.authenticate_key(s, RAW_KEY) is None


@pytest.mark.asyncio
async def test_rotate_key_invalidates_old():
    s = FakeSession()
    old = make_key()
    s.set_result([old])
    new_raw = await partners.rotate_key(s, make_account())
    assert old.status == "rotated" and old.rotated_at is not None
    new_keys = [x for x in s.added if isinstance(x, PartnerApiKey)]
    assert len(new_keys) == 1
    assert new_keys[0].key_hash == partners.hash_key(new_raw)
    assert new_keys[0].status == "active"


@pytest.mark.asyncio
async def test_revoke_keys_counts():
    s = FakeSession()
    keys = [make_key(), make_key()]
    s.set_result(keys)
    assert await partners.revoke_keys(s, make_account()) == 2
    assert all(k.status == "revoked" for k in keys)


# --- Сервис: claim / фидбек --------------------------------------------------


@pytest.mark.asyncio
async def test_claim_lead_ok_sets_fields():
    s = SeqSession()
    lead = make_lead(77)
    s.add(lead)
    s.set_queue([])  # claimed_today -> 0
    claimed, reason = await partners.claim_lead(s, make_account(), 77)
    assert reason == "ok" and claimed is lead
    assert lead.partner_id == 1 and lead.partner_status == "claimed"
    assert lead.claimed_at is not None


@pytest.mark.asyncio
async def test_claim_exclusive_second_partner_gets_taken():
    s = SeqSession()
    s.add(make_lead(77, partner_id=2, partner_status="claimed"))
    s.set_queue([])
    _, reason = await partners.claim_lead(s, make_account(), 77)
    assert reason == "taken"


@pytest.mark.asyncio
async def test_claim_already_yours_idempotent():
    s = SeqSession()
    s.add(make_lead(77, partner_id=1, partner_status="claimed"))
    s.set_queue([])
    claimed, reason = await partners.claim_lead(s, make_account(), 77)
    assert reason == "already_yours" and claimed.partner_id == 1


@pytest.mark.asyncio
async def test_claim_quota_exceeded():
    s = SeqSession()
    s.set_queue([1, 2, 3])  # claimed_today -> 3 при квоте 3
    _, reason = await partners.claim_lead(s, make_account(daily_quota=3), 77)
    assert reason == "quota_exceeded"


@pytest.mark.asyncio
async def test_claim_missing_lead_not_found():
    s = SeqSession()
    s.set_queue([])
    _, reason = await partners.claim_lead(s, make_account(), 999)
    assert reason == "not_found"


@pytest.mark.asyncio
async def test_feedback_only_own_lead():
    s = SeqSession()
    s.add(make_lead(77, partner_id=2))
    assert await partners.set_feedback(s, make_account(), 77, "deal") is None

    s.add(make_lead(78, partner_id=1, partner_status="claimed"))
    updated = await partners.set_feedback(s, make_account(), 78, "deal")
    assert updated is not None and updated.partner_status == "deal"


@pytest.mark.asyncio
async def test_available_leads_returns_pool():
    s = SeqSession()
    s.set_queue([make_lead(1), make_lead(2)])
    assert len(await partners.available_leads(s, limit=10)) == 2


# --- API: /api/partner (X-API-Key) -------------------------------------------


@pytest.fixture
def seq_session() -> SeqSession:
    return SeqSession()


@pytest.fixture
def client(seq_session: SeqSession, fake_redis: FakeRedis):
    from httpx import ASGITransport, AsyncClient

    app.dependency_overrides[get_session] = lambda: seq_session
    app.dependency_overrides[get_redis] = lambda: fake_redis
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_partner_api_requires_key(client):
    assert (await client.get("/api/partner/me")).status_code == 401


@pytest.mark.asyncio
async def test_partner_api_invalid_key_401(client, seq_session: SeqSession):
    seq_session.add(make_account())
    seq_session.set_queue([])  # ключ не найден по хэшу
    resp = await client.get("/api/partner/me", headers={"X-API-Key": RAW_KEY})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_partner_me_quota_state(client, seq_session: SeqSession):
    seq_session.add(make_account(daily_quota=5))
    seq_session.set_queue([make_key()], [["x"]])  # auth, claimed_today=1
    resp = await client.get("/api/partner/me", headers={"X-API-Key": RAW_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["quota_remaining"] == 4 and body["plan"] == "pro"


@pytest.mark.asyncio
async def test_partner_leads_pool_hides_user_id(client, seq_session: SeqSession):
    seq_session.add(make_account())
    seq_session.set_queue([make_key()], [make_lead(11), make_lead(12)])
    resp = await client.get("/api/partner/leads", headers={"X-API-Key": RAW_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(x["user_id"] is None for x in body)  # ПД — только после claim
    assert body[0]["qualification"]["budget_usd"] == 20000


@pytest.mark.asyncio
async def test_partner_leads_mine_shows_user_id(client, seq_session: SeqSession):
    # Мои лиды (UI/UX ТЗ 4.5): ПД доступны - лид захвачен эксклюзивно
    claimed_at = datetime.now(UTC)
    seq_session.add(make_account())
    seq_session.set_queue(
        [make_key()],
        [make_lead(31, partner_id=1, partner_status="claimed", claimed_at=claimed_at)],
    )
    resp = await client.get(
        "/api/partner/leads?mine=true", headers={"X-API-Key": RAW_KEY}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["user_id"] == 5
    assert body[0]["partner_status"] == "claimed"
    assert body[0]["claimed_at"] is not None


@pytest.mark.asyncio
async def test_claim_ok_returns_contact_and_event(client, seq_session: SeqSession):
    seq_session.add(make_account())
    seq_session.add(make_lead(77))
    seq_session.set_queue([make_key()], [])
    resp = await client.post(
        "/api/partner/leads/77/claim", headers={"X-API-Key": RAW_KEY}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == 5 and body["partner_status"] == "claimed"
    events = [x for x in seq_session.added if isinstance(x, AnalyticsEvent)]
    assert events and events[0].name == "partner_lead_claimed"


@pytest.mark.asyncio
async def test_claim_conflict_409(client, seq_session: SeqSession):
    seq_session.add(make_account())
    seq_session.add(make_lead(77, partner_id=2, partner_status="claimed"))
    seq_session.set_queue([make_key()], [])
    resp = await client.post(
        "/api/partner/leads/77/claim", headers={"X-API-Key": RAW_KEY}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_claim_quota_429(client, seq_session: SeqSession):
    seq_session.add(make_account(daily_quota=2))
    seq_session.set_queue([make_key()], [1, 2])
    resp = await client.post(
        "/api/partner/leads/77/claim", headers={"X-API-Key": RAW_KEY}
    )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_feedback_requires_own_lead(client, seq_session: SeqSession):
    seq_session.add(make_account())
    seq_session.add(make_lead(77, partner_id=2))
    seq_session.set_queue([make_key()])
    resp = await client.post(
        "/api/partner/leads/77/feedback",
        json={"status": "deal"},
        headers={"X-API-Key": RAW_KEY},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_feedback_ok_and_event(client, seq_session: SeqSession):
    seq_session.add(make_account())
    seq_session.add(make_lead(77, partner_id=1, partner_status="claimed"))
    seq_session.set_queue([make_key()])
    resp = await client.post(
        "/api/partner/leads/77/feedback",
        json={"status": "junk"},
        headers={"X-API-Key": RAW_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["partner_status"] == "junk"
    events = [x for x in seq_session.added if isinstance(x, AnalyticsEvent)]
    assert events[0].name == "partner_lead_feedback"
    assert events[0].props["feedback"] == "junk"


@pytest.mark.asyncio
async def test_feedback_invalid_status_422(client, seq_session: SeqSession):
    seq_session.add(make_account())
    seq_session.set_queue([make_key()])
    resp = await client.post(
        "/api/partner/leads/77/feedback",
        json={"status": "bogus"},
        headers={"X-API-Key": RAW_KEY},
    )
    assert resp.status_code == 422


# --- Admin API + rate limit --------------------------------------------------


@pytest.mark.asyncio
async def test_admin_create_partner_requires_internal_token(client):
    resp = await client.post("/api/admin/partners", json={"name": "ИП Иванов"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_create_partner_returns_raw_key_once(
    client, seq_session: SeqSession
):
    resp = await client.post(
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
    assert body["key"].startswith("pi_") and len(body["key"]) == 43
    stored = [x for x in seq_session.added if isinstance(x, PartnerApiKey)]
    assert stored and stored[0].key_hash != body["key"]  # хэш, не сырой ключ


@pytest.mark.asyncio
async def test_rate_limit_fixed_window(fake_redis: FakeRedis):
    get_settings().partner_rate_limit_per_min = 2
    try:
        await check_rate_limit(fake_redis, 1)
        await check_rate_limit(fake_redis, 1)
        with pytest.raises(Exception) as exc:  # noqa: PT011
            await check_rate_limit(fake_redis, 1)
        assert getattr(exc.value, "status_code", None) == 429
    finally:
        get_settings().partner_rate_limit_per_min = 60


@pytest.mark.asyncio
async def test_rate_limit_fail_open():
    class BrokenRedis:
        async def incr(self, key: str) -> int:
            raise ConnectionError("redis down")

        async def expire(self, key: str, ttl: int) -> bool:
            return False

    await check_rate_limit(BrokenRedis(), 1)  # не бросает — fail-open
# --- Сервис: batch-claim + биллинг ---------------------------------------------


@pytest.mark.asyncio
async def test_claim_batch_ok_sets_fields():
    s = SeqSession()
    l1, l2, l3 = make_lead(1), make_lead(2), make_lead(3)
    s.set_queue([], [l1, l2, l3])  # claimed_today -> 0, пул из 3
    leads, reason = await partners.claim_batch(s, make_account(), limit=3)
    assert reason == "ok" and len(leads) == 3
    assert all(lead.partner_id == 1 for lead in leads)
    assert all(lead.partner_status == "claimed" and lead.claimed_at for lead in leads)
    assert s.committed


@pytest.mark.asyncio
async def test_claim_batch_quota_exceeded():
    s = SeqSession()
    s.set_queue([1, 2, 3, 4, 5])  # claimed_today=5 при квоте 5 (scalar_one=len(rows))
    leads, reason = await partners.claim_batch(
        s, make_account(daily_quota=5), limit=3
    )
    assert reason == "quota_exceeded" and leads == []
    assert not s.committed


@pytest.mark.asyncio
async def test_claim_batch_partial_when_quota_low():
    s = SeqSession()
    s.set_queue([4], [make_lead(9)])  # остаток квоты 1, лимит 3
    leads, reason = await partners.claim_batch(s, make_account(), limit=3)
    assert reason == "ok" and len(leads) == 1


@pytest.mark.asyncio
async def test_batch_claim_endpoint_ok(client, seq_session: SeqSession):
    seq_session.add(make_account())
    seq_session.set_queue([make_key()], [], [make_lead(11), make_lead(12)])
    resp = await client.post(
        "/api/partner/leads/batch", json={"limit": 2}, headers={"X-API-Key": RAW_KEY}
    )
    assert resp.status_code == 200
    claimed = resp.json()["claimed"]
    assert len(claimed) == 2
    assert {c["lead_id"] for c in claimed} == {11, 12}
    events = [x for x in seq_session.added if isinstance(x, AnalyticsEvent)]
    assert len(events) == 2 and events[0].props["batch"] is True


@pytest.mark.asyncio
async def test_batch_claim_endpoint_quota_429(client, seq_session: SeqSession):
    seq_session.add(make_account(daily_quota=5))
    seq_session.set_queue([make_key()], [1, 2, 3, 4, 5])  # claimed_today -> 5
    resp = await client.post(
        "/api/partner/leads/batch", json={"limit": 3}, headers={"X-API-Key": RAW_KEY}
    )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_batch_claim_endpoint_unauthorized(client):
    resp = await client.post("/api/partner/leads/batch", json={"limit": 2})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_billing_endpoint_catalog_and_usage(client, seq_session: SeqSession):
    seq_session.add(make_account(plan="pro", daily_quota=15))
    seq_session.set_queue([make_key()], [1, 2, 3, 4])  # claimed_today -> 4
    resp = await client.get("/api/partner/billing", headers={"X-API-Key": RAW_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "pro"
    assert body["claimed_today"] == 4 and body["quota_remaining"] == 11
    assert {p["key"] for p in body["plans"]} == {"starter", "pro", "salon"}
    pro = next(p for p in body["plans"] if p["key"] == "pro")
    assert pro["price_usd"] == 149 and pro["daily_quota"] == 15


@pytest.mark.asyncio
async def test_billing_endpoint_unauthorized(client):
    resp = await client.get("/api/partner/billing")
    assert resp.status_code == 401

