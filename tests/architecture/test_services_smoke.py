"""Смоук-тесты доменных микросервисов (профиль micro).

Для каждого сервиса: приложение собирается, /health отвечает, состав
префиксов API ровно своего домена (ничего лишнего и ничего чужого),
и один представительный эндпоинт отрабатывает сквозь контейнер на
тестовых двойниках БД/Redis (auth — настоящий: JWT / X-API-Key).
"""

from types import SimpleNamespace
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import get_settings
from app.infrastructure.db.models import PartnerAccount, User
from app.infrastructure.security.tokens import create_access_token, create_refresh_token
from services.admin.main import create_app as create_admin
from services.billing.main import create_app as create_billing
from services.catalog.main import create_app as create_catalog
from services.dialog.main import create_app as create_dialog
from services.identity.main import create_app as create_identity
from services.partners.main import create_app as create_partners

from conftest import FakeRedis, FakeSession, install_test_container

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
    "catalog": {"/api/listings", "/api/favorites", "/api/filters", "/api/notifications", "/api/calc", "/api/bot"},
    "billing": {"/api/payments", "/api/subscription", "/api/users"},
    "partners": {"/api/partner", "/api/admin", "/api/leads"},
    "dialog": {"/api/dialog", "/api/ai"},
    "admin": {"/api/admin"},
}


def _headers(user_id: int = 111111) -> dict[str, str]:
    """Реальный Bearer-заголовок: auth-путь не подменяется."""
    return {
        "Authorization": f"Bearer {create_access_token(user_id, get_settings())}"
    }


def _iter_api_paths(routes, prefix: str = "") -> list[str]:
    """Все пути API-роутов приложения.

    Версионно-толерантно: свежий FastAPI кладёт include_router в
    `_IncludedRouter` (оригинальный роутер — в `.original_router`,
    префикс включения — в `.include_context.prefix`), старый
    разворачивает пути в app.routes; Mount хранит `.routes`.
    """
    paths: list[str] = []
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.append(prefix + path)
        original = getattr(route, "original_router", None)
        if original is not None:  # FastAPI с _IncludedRouter
            context = getattr(route, "include_context", None)
            sub_prefix = prefix + (getattr(context, "prefix", "") or "")
            paths.extend(_iter_api_paths(original.routes, sub_prefix))
        elif getattr(route, "routes", None) is not None:  # Mount
            paths.extend(_iter_api_paths(route.routes, prefix))
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


def test_smoke_admin_exposes_metrics_route():
    """Метрики мониторинга доступны в admin-домене (gateway проксирует /metrics)."""
    paths = _iter_api_paths(create_admin().routes)
    assert "/metrics" in paths
    assert "/metrics" not in _iter_api_paths(create_catalog().routes)


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


async def _client_for(app, session=None, redis=None) -> AsyncClient:
    """Подменить БД/Redis контейнером (auth ходит по-настоящему, без подмен)."""
    if session is not None or redis is not None:
        install_test_container(app, session=session, redis=redis)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://svc.test")


async def test_smoke_identity_refresh_endpoint():
    """identity: /api/auth/refresh выпускает новую пару токенов."""
    app = create_identity()
    session = FakeSession()
    session.add(
        User(telegram_id=42, username="u", subscription_tier="free", is_active=True)
    )
    async with await _client_for(app, session=session) as http:
        token = create_refresh_token(42, get_settings())
        resp = await http.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_smoke_catalog_listings_feed():
    """catalog: /api/listings отвечает (пустая выдача на FakeSession)."""
    app = create_catalog()
    async with await _client_for(
        app, session=FakeSession()
    ) as http:
        resp = await http.get("/api/listings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0 and body["items"] == []
    # keyset-поля аддитивны (фаза 9), старый контракт сохранён.
    assert body.get("has_more") is False
    assert body.get("next_cursor") is None


async def test_smoke_billing_subscription():
    """billing: /api/subscription отдаёт тариф и лимиты."""
    app = create_billing()
    session = FakeSession()
    session.add(User(telegram_id=111111, subscription_tier="free", is_active=True))
    async with await _client_for(app, session=session) as http:
        resp = await http.get("/api/subscription", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["max_filters"] == 3


async def test_smoke_partners_me():
    """partners: /api/partner/me отдаёт ЛК партнёра (аккаунт в FakeSession)."""
    app = create_partners()
    session = FakeSession()
    session.add(
        PartnerAccount(
            id=1,
            name="Партнёр",
            plan="starter",
            status="active",
            balance=Decimal("100.0"),
            daily_quota=10,
            cpl_rate=None,
        )
    )
    # PartnerApiKey-строка для выборки по хэшу (FakeSession игнорирует WHERE,
    # но первая строка обязана прийти первым execute-ом); аккаунт достаётся
    # через get(). Вторая группа — пустая: scalar_one() у count (claimed_today)
    # вернёт len(rows) = 0.
    session.set_results([[SimpleNamespace(id=1, status="active", partner_id=1)], []])
    async with await _client_for(app, session=session) as http:
        resp = await http.get("/api/partner/me", headers={"X-API-Key": "pi_smoke"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Партнёр"
    assert body["quota_remaining"] == 10
    # Decimal в схеме → строка в JSON (pydantic v2)
    assert float(body["balance"]) == 100.0


async def test_smoke_dialog_ai_reset():
    """dialog: /api/ai/reset проходит с JWT-юзером (user_id = telegram_id)."""
    app = create_dialog()
    session = FakeSession()
    session.add(User(telegram_id=111111, subscription_tier="free", is_active=True))
    async with await _client_for(app, session=session, redis=FakeRedis()) as http:
        resp = await http.post("/api/ai/reset", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}


async def test_smoke_admin_adapters(monkeypatch):
    """admin: /api/admin/adapters за internal-токеном (без БД)."""
    token_value = "test-internal-token"
    # Settings.internal_token — SecretStr; строка сломает internal_token()
    monkeypatch.setattr(get_settings(), "internal_token", SecretStr(token_value))
    app = create_admin()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://svc.test"
    ) as http:
        resp = await http.get(
            "/api/admin/adapters", headers={"X-Internal-Token": token_value}
        )
    assert resp.status_code == 200
    # ADAPTER_CLASSES — это классы адаптеров, а не имена источников
    assert "EncarApiAdapter" in resp.json()["adapters"]

