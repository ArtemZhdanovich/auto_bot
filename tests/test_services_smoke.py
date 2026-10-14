"""Смоук-тесты доменных микросервисов (профиль micro).

Для каждого сервиса: приложение собирается, /health отвечает, состав
префиксов API ровно своего домена (ничего лишнего и ничего чужого),
и один представительный эндпоинт отрабатывает сквозь реальные
dependency-overrides (как в монолитных тестах, но на изолированном app).
"""

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.core.deps_internal import get_redis
from app.core.deps_partner import require_partner
from app.core.security import create_refresh_token
from app.db.models import User
from app.db.session import get_session
from services.admin.main import create_app as create_admin
from services.billing.main import create_app as create_billing
from services.catalog.main import create_app as create_catalog
from services.dialog.main import create_app as create_dialog
from services.identity.main import create_app as create_identity
from services.partners.main import create_app as create_partners

from conftest import FakeRedis, FakeSession

SERVICES = {
    "identity": create_identity,
    "catalog": create_catalog,
    "billing": create_billing,
    "partners": create_partners,
    "dialog": create_dialog,
    "admin": create_admin,
}

# Ровно те префиксы API, которые обязан обслуживать каждый сервис
EXPECTED_PREFIXES: dict[str, set[str]] = {
    "identity": {"/api/auth", "/api/users", "/api/me"},
    "catalog": {"/api/listings", "/api/favorites", "/api/filters", "/api/notifications"},
    "billing": {"/api/payments", "/api/subscription", "/api/users"},
    "partners": {"/api/partner", "/api/admin", "/api/leads"},
    "dialog": {"/api/dialog", "/api/ai"},
    "admin": {"/api/admin"},
}

TEST_USER = SimpleNamespace(
    telegram_id=111111, subscription_tier="free", subscription_until=None
)


def _iter_api_paths(routes) -> list[str]:
    """Все пути API-роутов приложения.

    Версионно-толерантно: свежий FastAPI кладёт include_router в
    `_IncludedRouter` (оригинальный роутер — в `.original_router`),
    старый разворачивает пути в app.routes; Mount хранит `.routes`.
    """
    paths: list[str] = []
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.append(path)
        original = getattr(route, "original_router", None)
        if original is not None:  # FastAPI с _IncludedRouter
            paths.extend(_iter_api_paths(original.routes))
        elif getattr(route, "routes", None) is not None:  # Mount
            paths.extend(_iter_api_paths(route.routes))
    return paths


def _api_prefixes(app) -> set[str]:
    """Топ-уровневые префиксы /api/*: /api/auth/telegram → /api/auth."""
    return {
        "/".join(path.split("/")[:3])
        for path in _iter_api_paths(app.routes)
        if path.startswith("/api/")
    }


@pytest.mark.parametrize("name", sorted(SERVICES))
async def test_smoke_health(name: str):
    """Смоук: приложение собирается и отвечает на /health."""
    app = SERVICES[name]()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://svc.test"
    ) as http:
        resp = await http.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "service": name,
        "env": get_settings().app_env,
    }


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_smoke_domain_boundaries(name: str):
    """Смоук: сервис обслуживает ровно свой домен — без чужих роутов."""
    app = SERVICES[name]()
    assert _api_prefixes(app) == EXPECTED_PREFIXES[name]


def test_smoke_no_context_leaks_across_services():
    """Ключевые пути каждого контекста есть только у одного сервиса."""
    paths = {
        name: set(_iter_api_paths(SERVICES[name]().routes))
        for name in SERVICES
    }

    def owners(prefix: str) -> set[str]:
        return {
            name
            for name, route_set in paths.items()
            if any(p.startswith(prefix) for p in route_set)
        }

    assert owners("/api/listings") == {"catalog"}
    assert owners("/api/dialog") == {"dialog"}
    assert owners("/api/payments") == {"billing"}
    assert owners("/api/partner") == {"partners"}
    assert owners("/api/auth") == {"identity"}
    # billing легитимно отдаёт внутренний тариф (payments-роутер), identity — остальное
    assert owners("/api/users") == {"identity", "billing"}


async def _client_for(app, overrides: dict) -> AsyncClient:
    for dependency, replacement in overrides.items():
        app.dependency_overrides[dependency] = replacement
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://svc.test")


async def test_smoke_identity_refresh_endpoint():
    """identity: /api/auth/refresh выпускает новую пару токенов."""
    app = create_identity()
    session = FakeSession()
    session.add(
        User(telegram_id=42, username="u", subscription_tier="free", is_active=True)
    )
    async with await _client_for(app, {get_session: lambda: session}) as http:
        token = create_refresh_token(42, get_settings())
        resp = await http.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_smoke_catalog_listings_feed():
    """catalog: /api/listings отвечает (пустая выдача на FakeSession)."""
    app = create_catalog()
    async with await _client_for(
        app, {get_session: lambda: FakeSession()}
    ) as http:
        resp = await http.get("/api/listings")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "items": []}


async def test_smoke_billing_subscription():
    """billing: /api/subscription отдаёт тариф и лимиты."""
    app = create_billing()
    async with await _client_for(
        app, {get_current_user: lambda: TEST_USER}
    ) as http:
        resp = await http.get("/api/subscription")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["max_filters"] == 3


async def test_smoke_partners_me():
    """partners: /api/partner/me отдаёт ЛК партнёра (ключ из override)."""
    app = create_partners()
    key = SimpleNamespace(key_prefix="pi_deadbeef…")
    account = SimpleNamespace(
        id=1,
        name="Партнёр",
        plan="starter",
        daily_quota=10,
        balance=100.0,
        cpl_rate=None,
    )
    async with await _client_for(
        app,
        {
            require_partner: lambda: (key, account),
            get_session: lambda: FakeSession(),
        },
    ) as http:
        resp = await http.get("/api/partner/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Партнёр"
    assert body["quota_remaining"] == 10
    assert body["balance"] == 100.0


async def test_smoke_dialog_ai_reset():
    """dialog: /api/ai/reset проходит с JWT-юзером (user_id = telegram_id)."""
    app = create_dialog()
    async with await _client_for(
        app,
        {
            get_current_user: lambda: TEST_USER,
            get_session: lambda: FakeSession(),
            get_redis: lambda: FakeRedis(),
        },
    ) as http:
        resp = await http.post("/api/ai/reset")
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}


async def test_smoke_admin_adapters(monkeypatch):
    """admin: /api/admin/adapters за internal-токеном (без БД)."""
    token = "test-internal-token"
    monkeypatch.setattr(get_settings(), "internal_token", token)
    app = create_admin()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://svc.test"
    ) as http:
        resp = await http.get(
            "/api/admin/adapters", headers={"X-Internal-Token": token}
        )
    assert resp.status_code == 200
    # ADAPTER_CLASSES — это классы адаптеров, а не имена источников
    assert "EncarApiAdapter" in resp.json()["adapters"]

