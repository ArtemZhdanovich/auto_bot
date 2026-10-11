"""Тесты эндпоинтов Mini App (этап 5): CRUD фильтров и лента объявлений."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.deps import get_current_user
from app.db.session import get_session
from app.main import app

from conftest import FakeSession

TEST_USER = SimpleNamespace(
    telegram_id=111111, subscription_tier="free", subscription_until=None
)


def _pro_user() -> SimpleNamespace:
    """Pro-подписка на 30 дней вперёд."""
    until = datetime.fromtimestamp(datetime.now(UTC).timestamp() + 30 * 86400, tz=UTC)
    return SimpleNamespace(
        telegram_id=111111, subscription_tier="pro", subscription_until=until
    )


@pytest.fixture
def client(fake_redis):  # noqa: ARG001
    from httpx import ASGITransport, AsyncClient

    session = FakeSession()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: TEST_USER

    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), session
    app.dependency_overrides.clear()


# ---------- Фильтры ----------


@pytest.mark.asyncio
async def test_create_filter_requires_auth(client):
    http, _ = client
    app.dependency_overrides.pop(get_current_user, None)
    resp = await http.post("/api/filters", json={"name": "BMW"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_filter_201(client):
    http, session = client
    resp = await http.post(
        "/api/filters",
        json={
            "name": "BMW до 20к",
            "criteria": {"make": "BMW", "price_max": 20000},
            "notify_mode": "digest",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == 1
    assert body["criteria"]["make"] == "BMW"
    assert body["notify_mode"] == "digest"
    # criteria сохранён без None-полей
    assert "fuel" not in body["criteria"]
    assert len(session.added) == 1
    assert session.committed is True


@pytest.mark.asyncio
async def test_instant_forbidden_on_free(client):
    """Instant — фича Pro (ТЗ п. 10.2): Free получает 403."""
    http, _ = client
    resp = await http.post(
        "/api/filters",
        json={"criteria": {"make": "BMW"}, "notify_mode": "instant"},
    )
    assert resp.status_code == 403
    assert "Pro" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_instant_allowed_on_pro():
    """Pro-пользователь может создать instant-фильтр."""
    from httpx import ASGITransport, AsyncClient

    from app.db.session import get_session

    session = FakeSession()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: _pro_user()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.post(
                "/api/filters",
                json={"criteria": {"make": "BMW"}, "notify_mode": "instant"},
            )
        assert resp.status_code == 201
        assert resp.json()["notify_mode"] == "instant"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_filter_limit_free_is_3(client):
    """Free: 3 фильтра, четвёртый — 409 с апселлом (ТЗ п. 10.2)."""
    http, session = client
    session.set_result(
        [
            SimpleNamespace(id=i, user_id=111111, criteria={})
            for i in (1, 2, 3)
        ]
    )
    resp = await http.post(
        "/api/filters", json={"name": "четвёртый", "criteria": {}}
    )
    assert resp.status_code == 409
    assert "Pro" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_filter_invalid_criteria_422(client):
    http, _ = client
    resp = await http.post(
        "/api/filters",
        json={"criteria": {"year_from": "invalid"}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_filters_maps_orm(client):
    http, session = client
    session.set_result(
        [
            SimpleNamespace(
                id=7,
                name="f1",
                criteria={"make": "Audi"},
                notify_mode="digest",
                quiet_hours=None,
                created_at=datetime.now(UTC),
                updated_at=None,
            )
        ]
    )
    resp = await http.get("/api/filters")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == 7


@pytest.mark.asyncio
async def test_update_filter_not_found_404(client):
    http, _ = client
    resp = await http.patch("/api/filters/99", json={"name": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_filter_marks_deleted(client):
    from app.db.models import UserFilter

    http, session = client
    session.add(UserFilter(user_id=TEST_USER.telegram_id, criteria={}))
    resp = await http.delete("/api/filters/1")
    assert resp.status_code == 204
    assert len(session.deleted) == 1


# ---------- Лента объявлений ----------


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
async def test_list_listings(client):
    http, session = client
    session.set_result([_listing(1), _listing(2)])
    resp = await http.get("/api/listings?make=bmw&price_max=20000&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == [1, 2]
    assert body["items"][0]["make"] == "BMW"
    # Лента без истории цен (только карточка, иначе N+1)
    assert body["items"][0]["price_history"] is None


@pytest.mark.asyncio
async def test_list_listings_validates_params(client):
    http, _ = client
    resp = await http.get("/api/listings?limit=999")
    assert resp.status_code == 422
    resp = await http.get("/api/listings?offset=-1")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_listing_404(client):
    http, _ = client
    resp = await http.get("/api/listings/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_listing_with_price_history(client):
    """Карточка включает price_history (хронологический порядок), PLAN.md фаза 4."""
    from app.db.models import Listing

    http, session = client
    session.add(
        Listing(
            id=5,
            source="encar",
            source_id="s-5",
            url="https://encar.com/x",
            make="BMW",
            model="520i",
            year=2018,
            price=17000.0,
            currency="USD",
            images=[],
            parsed_at=datetime.now(UTC),
            is_active=True,  # серверный дефолт БД; до flush в ORM-объекте None
        )
    )
    now = datetime.now(UTC)
    session.set_results(
        [
            [
                SimpleNamespace(price=18000.0, currency="USD", recorded_at=now),
                SimpleNamespace(price=17500.0, currency="USD", recorded_at=now),
                SimpleNamespace(price=17000.0, currency="USD", recorded_at=now),
            ]
        ]
    )
    resp = await http.get("/api/listings/5")
    assert resp.status_code == 200
    body = resp.json()
    history = body["price_history"]
    assert [p["price"] for p in history] == [18000.0, 17500.0, 17000.0]
    assert body["price"] == 17000.0
