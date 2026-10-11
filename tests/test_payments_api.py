"""Тесты подписок и Telegram Stars (этап 6, ТЗ п. 8.7, 10.2) + избранного."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.deps import get_current_user
from app.db.models import Payment
from app.db.session import get_session
from app.main import app

from conftest import FakeSession

TEST_USER = SimpleNamespace(
    telegram_id=111111,
    subscription_tier="free",
    subscription_until=None,
)


@pytest.fixture
def client(fake_redis):  # noqa: ARG001
    from httpx import ASGITransport, AsyncClient

    session = FakeSession()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: TEST_USER

    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), session
    app.dependency_overrides.clear()


# ---------- GET /api/subscription ----------


@pytest.mark.asyncio
async def test_subscription_status_free(client):
    http, _ = client
    resp = await http.get("/api/subscription")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["max_filters"] == 3
    assert body["instant"] is False
    assert body["prices"]["pro_month"] == 500
    assert body["prices"]["pro_year"] == 4800  # −20%
    assert body["prices"]["business_month"] == 2500


@pytest.mark.asyncio
async def test_subscription_status_expired_pro_is_free(client):
    """Истёкшая подписка = действующий Free (ТЗ п. 8.7)."""
    from httpx import ASGITransport, AsyncClient

    expired = SimpleNamespace(
        telegram_id=111111,
        subscription_tier="pro",
        subscription_until=datetime.now(UTC) - timedelta(days=1),
    )
    app.dependency_overrides[get_current_user] = lambda: expired
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.get("/api/subscription")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["paid_tier"] == "pro"


# ---------- POST /api/payments/stars ----------


@pytest.mark.asyncio
async def test_stars_invoice_creates_pending_payment(client, monkeypatch):
    """Инвойс: pending-платёж в БД + ссылка от createInvoiceLink (ТЗ п. 9.6)."""
    from app.api import payments as payments_api

    async def fake_link(**kwargs):
        assert kwargs["payload"] == "sub:1"
        assert kwargs["amount"] == 4800
        return "https://t.me/$4800invoice"

    monkeypatch.setattr(payments_api, "_create_invoice_link", fake_link)
    http, session = client
    resp = await http.post(
        "/api/payments/stars", json={"tier": "pro", "period": "year"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["invoice_url"] == "https://t.me/$4800invoice"
    assert body["amount"] == 4800
    assert body["period_days"] == 365
    payment = session.added[0]
    assert isinstance(payment, Payment)
    assert payment.status == "pending"
    assert payment.currency == "XTR"
    assert payment.tier == "pro"


@pytest.mark.asyncio
async def test_stars_invoice_rejects_free_tier(client):
    http, _ = client
    resp = await http.post("/api/payments/stars", json={"tier": "free"})
    assert resp.status_code == 422  # Literal["pro", "business"]



# ---------- internal: prepare + success ----------


@pytest.mark.asyncio
async def test_prepare_payment_internal(client):
    http, session = client
    resp = await http.post(
        "/api/payments/prepare",
        json={"user_id": 809464979, "tier": "business", "period_days": 30},
        headers={"X-Internal-Token": "change-me"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["amount"] == 2500
    assert body["title"] == "AutoPick Business"
    assert session.added[0].status == "pending"


@pytest.mark.asyncio
async def test_prepare_requires_internal_token(client):
    http, _ = client
    resp = await http.post(
        "/api/payments/prepare", json={"user_id": 1, "tier": "pro", "period_days": 30}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_payment_success_activates_subscription(client):
    """successful_payment: платёж → paid, подписка активна, идемпотентно."""
    http, session = client
    payment = Payment(
        id=1,
        user_id=111111,
        amount=500,
        currency="XTR",
        provider="stars",
        status="pending",
        tier="pro",
        period_days=30,
    )
    session.add(payment)
    user = SimpleNamespace(
        telegram_id=111111, subscription_tier="free", subscription_until=None
    )

    async def _get(model, pk):
        if model is Payment:
            return payment
        if model.__name__ == "User":
            return user
        return None

    session.get = _get  # type: ignore[method-assign]

    body = {
        "payment_id": 1,
        "user_id": 111111,
        "provider_payment_id": "chg_123",
        "amount": 500,
    }
    resp = await http.post(
        "/api/payments/success", json=body, headers={"X-Internal-Token": "change-me"}
    )
    assert resp.status_code == 200
    assert resp.json()["activated"] is True
    assert payment.status == "paid"
    assert payment.provider_payment_id == "chg_123"
    assert user.subscription_tier == "pro"
    assert user.subscription_until > datetime.now(UTC)

    # retry того же successful_payment не продлевает повторно
    until_before = user.subscription_until
    resp2 = await http.post(
        "/api/payments/success", json=body, headers={"X-Internal-Token": "change-me"}
    )
    assert resp2.status_code == 200
    assert user.subscription_until == until_before


# ---------- Избранное ----------


def _listing(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=i,
        source="encar",
        source_id=f"s-{i}",
        url="https://encar.com/x",
        make="BMW",
        model="520i",
        year=2018,
        price=17000.0,
        currency="USD",
        mileage_km=85000,
        vin=None,
        images=[],
        parsed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_favorite_toggle(client):
    """Toggle: добавил → favorited=true, повторно → false (ТЗ п. 9.2.4)."""
    http, session = client

    async def _get(model, pk):  # type: ignore[no-untyped-def]
        return _listing(1) if model.__name__ == "Listing" else None

    session.get = _get  # type: ignore[method-assign]
    session.set_result([])  # избранного ещё нет
    resp = await http.post("/api/listings/1/favorite")
    assert resp.status_code == 200
    assert resp.json() == {"favorited": True}
    assert len(session.added) == 1

    session.set_result([SimpleNamespace(user_id=111111, listing_id=1)])
    resp = await http.post("/api/listings/1/favorite")
    assert resp.json() == {"favorited": False}
    assert len(session.deleted) == 1


@pytest.mark.asyncio
async def test_favorite_unknown_listing_404(client):
    http, session = client

    async def _get(model, pk):  # type: ignore[no-untyped-def]
        return None

    session.get = _get  # type: ignore[method-assign]
    resp = await http.post("/api/listings/999/favorite")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_favorites(client):
    http, session = client
    session.set_result([_listing(1), _listing(2)])
    resp = await http.get("/api/favorites")
    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()] == [1, 2]
