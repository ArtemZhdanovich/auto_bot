"""Тесты эндпоинтов Mini App (этап 5): CRUD фильтров и лента объявлений."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.infrastructure.db.models import User
from app.infrastructure.security.tokens import create_access_token
from app.main_api import app

from conftest import FakeSession, install_test_container

TG_ID = 111111


class _CommitOnExit:
    """Контекст session.begin(): коммитит при выходе без исключения."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *exc_info) -> bool:
        if exc_info[0] is None:
            self._session.committed = True
        return False


class _SessionWithBegin(FakeSession):
    """FakeSession + async with begin(): create_filter пишет в транзакции."""

    def begin(self) -> _CommitOnExit:
        return _CommitOnExit(self)


def _user(**overrides) -> User:
    """Базовый free-пользователь мини-аппа (id TG_ID)."""
    fields: dict = {"telegram_id": TG_ID, "is_active": True}
    fields.update(overrides)
    return User(**fields)


def _headers(user_id: int = TG_ID) -> dict[str, str]:
    """Реальный Bearer-заголовок: auth-путь не подменяется."""
    return {
        "Authorization": f"Bearer {create_access_token(user_id, get_settings())}"
    }


@pytest.fixture
def client(fake_redis):  # noqa: ARG001
    from httpx import ASGITransport, AsyncClient

    session = _SessionWithBegin()
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


# ---------- Фильтры ----------


@pytest.mark.asyncio
async def test_create_filter_requires_auth():
    """Запрос без Bearer-токена — 401 (auth не подменяется)."""
    from httpx import ASGITransport, AsyncClient

    session = _SessionWithBegin()
    _prev = app.state.dishka_container
    install_test_container(app, session=session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.post("/api/filters", json={"name": "BMW"})
        assert resp.status_code == 401
    finally:
        app.state.dishka_container = _prev


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
    # в сессии: юзер (из фикстуры) + созданный фильтр
    assert len([o for o in session.added if o.__class__.__name__ == "UserFilter"]) == 1
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
async def test_instant_allowed_on_business():
    """Business-пользователь может создать instant-фильтр (Pro — дайджест)."""
    from httpx import ASGITransport, AsyncClient

    
    until = datetime.fromtimestamp(datetime.now(UTC).timestamp() + 30 * 86400, tz=UTC)
    session = _SessionWithBegin()
    session.add(_user(subscription_tier="business", subscription_until=until))
    _prev = app.state.dishka_container
    _container = install_test_container(app, session=session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=_headers()
        ) as http:
            resp = await http.post(
                "/api/filters",
                json={"criteria": {"make": "BMW"}, "notify_mode": "instant"},
            )
        assert resp.status_code == 201
        assert resp.json()["notify_mode"] == "instant"
    finally:
        app.state.dishka_container = _prev
        await _container.close()


@pytest.mark.asyncio
async def test_instant_forbidden_on_pro_daily_digest_instead():
    """Экономика 2026-10: instant только Business; Pro получает 403 с апселлом."""
    from httpx import ASGITransport, AsyncClient

    until = datetime.fromtimestamp(datetime.now(UTC).timestamp() + 30 * 86400, tz=UTC)
    session = _SessionWithBegin()
    session.add(_user(subscription_tier="pro", subscription_until=until))
    _prev = app.state.dishka_container
    _container = install_test_container(app, session=session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=_headers()
        ) as http:
            resp = await http.post(
                "/api/filters",
                json={"criteria": {"make": "BMW"}, "notify_mode": "instant"},
            )
        assert resp.status_code == 403
    finally:
        app.state.dishka_container = _prev
        await _container.close()


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
async def test_foreign_filter_returns_404(client):
    """IDOR: чужой фильтр неотличим от отсутствующего — 404 (PATCH и DELETE)."""
    from app.infrastructure.db.models import UserFilter

    http, session = client
    session.add(UserFilter(user_id=TG_ID + 1, criteria={}))
    foreign_id = session.added[-1].id

    resp = await http.patch(f"/api/filters/{foreign_id}", json={"name": "x"})
    assert resp.status_code == 404

    resp = await http.delete(f"/api/filters/{foreign_id}")
    assert resp.status_code == 404
    assert session.deleted == []


@pytest.mark.asyncio
async def test_delete_filter_marks_deleted(client):
    from app.infrastructure.db.models import UserFilter

    http, session = client
    session.add(UserFilter(user_id=TG_ID, criteria={}))
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
async def test_list_listings_sort_param(client):
    """sort=price_asc принимается; неизвестный ключ — 422 (PLP-сортировка)."""
    http, session = client
    session.set_result([_listing(1)])
    resp = await http.get("/api/listings?sort=price_asc")
    assert resp.status_code == 200
    resp = await http.get("/api/listings?sort=hacker")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_listings_mileage_min(client):
    """Пробег «от» (как на avtokorea.by) принимается фильтром ленты."""
    http, session = client
    session.set_result([_listing(1)])
    resp = await http.get("/api/listings?mileage_min=50000&mileage_max=100000")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_listings_facets_makes(client):
    """GET /api/listings/facets — марки + страны (GEO-V1 §2) для селекта."""
    http, session = client
    # Первый execute — страны (частые первыми), второй — марки.
    session.set_results([[("KR", 3), ("CN", 1)], [("BMW",), ("Kia",)]])
    resp = await http.get("/api/listings/facets")
    assert resp.status_code == 200
    assert resp.json() == {
        "makes": ["BMW", "Kia"],
        "countries": ["KR", "CN"],
    }


@pytest.mark.asyncio
async def test_listings_facets_models_for_make(client):
    """facets?make=X — только модели этой марки (зависимый селект)."""
    http, session = client
    session.set_results([[("KR", 2)], [("520i",), ("X5",)]])
    resp = await http.get("/api/listings/facets?make=bmw")
    assert resp.status_code == 200
    assert resp.json() == {"models": ["520i", "X5"], "countries": ["KR"]}


@pytest.mark.asyncio
async def test_list_listings_volume_color_filters(client):
    """Фильтры «объём от/до» и «цвет» (миграция 0017) принимаются."""
    http, session = client
    session.set_result([_listing(1)])
    resp = await http.get(
        "/api/listings?engine_volume_min=1.6&engine_volume_max=2.5&color=blue"
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    # Некорректный объём — 422 (ge=0/le=20)
    resp = await http.get("/api/listings?engine_volume_max=99")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_listing_404(client):
    http, _ = client
    resp = await http.get("/api/listings/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_listing_with_price_history(client):
    """Карточка включает price_history (хронологический порядок), PLAN.md фаза 4."""
    from app.infrastructure.db.models import Listing

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


# ---------- GET /api/listings/by-url (бот: «скинь ссылку» → карточка) ----------


def _listing_row(**kw):
    from datetime import UTC, datetime

    from app.infrastructure.db.models import Listing

    base = dict(
        id=9,
        source="encar",
        source_id="s-9",
        url="https://encar.com/car/12345678",
        make="Kia",
        model="Carnival",
        year=2021,
        price=32000.0,
        currency="USD",
        images=[],
        parsed_at=datetime.now(UTC),
        is_active=True,
    )
    base.update(kw)
    return Listing(**base)


@pytest.mark.asyncio
async def test_by_url_found(client):
    http, session = client
    session.set_result([_listing_row()])
    resp = await http.get(
        "/api/listings/by-url", params={"url": "https://m.encar.com/car/12345678/"}
    )
    assert resp.status_code == 200
    assert resp.json()["source_id"] == "s-9"
    assert resp.json()["make"] == "Kia"


@pytest.mark.asyncio
async def test_by_url_404(client):
    http, session = client
    session.set_result([])
    resp = await http.get(
        "/api/listings/by-url", params={"url": "https://encar.com/car/000"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_by_url_validation(client):
    http, _session = client
    resp = await http.get("/api/listings/by-url", params={"url": "x"})
    assert resp.status_code == 422


def test_norm_url_variants():
    from app.transport.http.routers.listings import _norm_url

    assert _norm_url("HTTPS://WWW.Encar.com/car/1/") == "encar.com/car/1"
    assert _norm_url("http://m.encar.com/car/1") == "encar.com/car/1"
    assert _norm_url("  encar.com/car/1  ") == "encar.com/car/1"


# ---------- Осмотр (миграция 0018): карта повреждений + чек-лист ----------


@pytest.mark.asyncio
async def test_inspection_404_when_absent():
    """Нет отчёта осмотра — 404 (фронт молча скрывает блок).

    Пользователь — pro (чеки платные: free получил бы 403); тестовая
    сессия через install_test_container, как в тарифных тестах фильтров.
    """
    from httpx import ASGITransport, AsyncClient

    until = datetime.now(UTC) + timedelta(days=30)
    session = _SessionWithBegin()
    session.add(_user(subscription_tier="pro", subscription_until=until))
    _prev = app.state.dishka_container
    _container = install_test_container(app, session=session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=_headers()
        ) as http:
            resp = await http.get("/api/listings/42/inspection")
        assert resp.status_code == 404
    finally:
        app.state.dishka_container = _prev
        await _container.close()


@pytest.mark.asyncio
async def test_inspection_locked_for_free(client):
    """Экономика 2026-10: чеки — платные данные, Free получает 403."""
    http, _session = client
    resp = await http.get("/api/listings/42/inspection")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_inspection_put_forbidden_for_non_admin(client):
    """PUT осмотра — только admin (ручной осмотр)."""
    http, session = client
    session.add(_user(telegram_id=1, role="buyer"))
    resp = await http.put(
        "/api/listings/42/inspection",
        json={"marks": [], "items": []},
        headers=_headers(1),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_inspection_put_404_without_listing(client):
    """Несуществующее объявление — 404 даже у админа."""
    http, session = client
    session.add(_user(telegram_id=1, role="admin"))
    resp = await http.put(
        "/api/listings/42/inspection", json={}, headers=_headers(1)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_inspection_upsert_roundtrip(client):
    """Админ создаёт отчёт: marks/items сохраняются, схема валидируется."""
    from typing import Any

    http, session = client
    admin = _user(telegram_id=1, role="admin")
    session.add(admin)
    listing = SimpleNamespace(id=42, source="encar")
    report_holder: dict[str, Any] = {}

    async def fake_get(model: Any, pk: Any) -> Any:
        if model is User:
            return admin
        if model.__name__ == "Listing":
            return listing if pk == 42 else None
        if model.__name__ == "InspectionReport":
            return report_holder.get("report")
        return None

    async def fake_refresh(obj: Any) -> None:
        return None

    session.get = fake_get  # type: ignore[method-assign]
    session.refresh = fake_refresh  # type: ignore[method-assign]
    real_add = session.add

    def add(obj: Any) -> None:
        real_add(obj)
        if obj.__class__.__name__ == "InspectionReport":
            report_holder["report"] = obj

    session.add = add  # type: ignore[method-assign]
    try:
        payload = {
            "source": "manual",
            "inspector": "Иван",
            "grade": "good",
            "summary": "Машина в хорошем состоянии",
            "marks": [
                {"zone": "front_bumper", "type": "scratch", "note": "скол на кромке"},
                {"zone": "door_rl", "type": "dent"},
            ],
            "items": [
                {"key": "engine", "status": "ok"},
                {"key": "brakes", "status": "warn", "note": "передние колодки"},
            ],
        }
        resp = await http.put(
            "/api/listings/42/inspection", json=payload, headers=_headers(1)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["grade"] == "good"
        assert len(body["marks"]) == 2
        assert body["marks"][0]["zone"] == "front_bumper"
        assert body["items"][1]["status"] == "warn"
        assert session.committed is True

        # Валидация: неизвестная зона — 422
        bad = dict(payload)
        bad["marks"] = [{"zone": "wheel", "type": "scratch"}]
        resp = await http.put(
            "/api/listings/42/inspection", json=bad, headers=_headers(1)
        )
        assert resp.status_code == 422
    finally:
        del session.get  # type: ignore[attr-defined]
