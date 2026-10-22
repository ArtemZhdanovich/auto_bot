"""Тесты подписок и Telegram Stars (этап 6, ТЗ п. 8.7, 10.2) + избранного."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.infrastructure.db.models import Payment, User
from app.infrastructure.security.tokens import create_access_token
from app.main_api import app

from conftest import FakeSession, install_test_container

INTERNAL_TOKEN = "test-internal-token"
TG_ID = 111111


def _user(**overrides) -> User:
    """Базовый free-пользователь (id TG_ID)."""
    fields: dict = {"telegram_id": TG_ID, "is_active": True}
    fields.update(overrides)
    return User(**fields)


def _headers(user_id: int = TG_ID) -> dict[str, str]:
    """Реальный Bearer-заголовок: auth-путь не подменяется."""
    return {
        "Authorization": f"Bearer {create_access_token(user_id, get_settings())}"
    }


@pytest.fixture(autouse=True)
def _legacy_favorites_path(monkeypatch):
    """Модуль проверяет legacy-ветку: FakeSession не умеет keyset-запросы.

    Прод-путь (use_case_favorites=True) покрыт test_favorites_use_case.py
    и test_favorites_list_use_case.py на реальной БД.
    """
    monkeypatch.setattr(get_settings(), "use_case_favorites", False)


@pytest.fixture
def internal_env(monkeypatch):
    """Детерминированные настройки для платёжных/internal тестов.

    Явно задаём INTERNAL_TOKEN и BOT_TOKEN, чтобы тесты не зависели от
    переменных окружения и наличия .env (в проде оба обязаны быть заданы).
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_token", SecretStr(INTERNAL_TOKEN))
    monkeypatch.setattr(settings, "bot_token", SecretStr("123:test"))


@pytest.fixture
def internal_headers(internal_env) -> dict[str, str]:
    """Заголовки под internal_env: токен совпадает с замоканным INTERNAL_TOKEN."""
    return {"X-Internal-Token": INTERNAL_TOKEN}


@pytest.fixture
def client(fake_redis):  # noqa: ARG001
    from httpx import ASGITransport, AsyncClient

    session = FakeSession()
    session.add(_user())
    _prev = app.state.dishka_container
    install_test_container(app, session=session)

    yield (
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=_headers()
        ),
        session,
    )
    app.state.dishka_container = _prev


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

    session = FakeSession()
    session.add(
        _user(
            subscription_tier="pro",
            subscription_until=datetime.now(UTC) - timedelta(days=1),
        )
    )
    _prev = app.state.dishka_container
    install_test_container(app, session=session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=_headers()
        ) as http:
            resp = await http.get("/api/subscription")
    finally:
        app.state.dishka_container = _prev
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["paid_tier"] == "pro"


# ---------- POST /api/payments/stars ----------


@pytest.mark.asyncio
async def test_stars_invoice_creates_pending_payment(client, internal_env, monkeypatch):
    """Инвойс: pending-платёж в БД + ссылка от createInvoiceLink (ТЗ п. 9.6).

    internal_env задаёт BOT_TOKEN (без него эндпоинт отвечает 503).
    """
    from app.transport.http.routers import payments as payments_api

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
    payment = next(o for o in session.added if isinstance(o, Payment))
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
async def test_prepare_payment_internal(client, internal_headers):
    http, session = client
    resp = await http.post(
        "/api/payments/prepare",
        json={"user_id": 809464979, "tier": "business", "period_days": 30},
        headers=internal_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["amount"] == 2500
    assert body["title"] == "AutoPick Business"
    assert next(o for o in session.added if isinstance(o, Payment)).status == "pending"


@pytest.mark.asyncio
async def test_prepare_requires_internal_token(client):
    http, _ = client
    resp = await http.post(
        "/api/payments/prepare", json={"user_id": 1, "tier": "pro", "period_days": 30}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_payment_success_activates_subscription(client, internal_headers):
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
        "/api/payments/success", json=body, headers=internal_headers
    )
    assert resp.status_code == 200
    assert resp.json()["activated"] is True
    assert payment.status == "paid"
    assert payment.provider_payment_id == "chg_123"
    assert user.subscription_tier == "pro"
    assert user.subscription_until > datetime.now(UTC)

    # retry того же successful_payment не продлевает повторно
    until_before = user.subscription_until
    resp2 = await http.post("/api/payments/success", json=body, headers=internal_headers)
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


class _ResultWithRowcount:
    """Результат execute() с rowcount — как у INSERT/DELETE в реальной БД."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount




@pytest.mark.asyncio
async def test_favorite_unknown_listing_404(client):
    http, session = client

    async def _get(model, pk):  # type: ignore[no-untyped-def]
        if model is User:
            return _user()
        return None

    session.get = _get  # type: ignore[method-assign]
    resp = await http.post("/api/listings/999/favorite")
    assert resp.status_code == 404


