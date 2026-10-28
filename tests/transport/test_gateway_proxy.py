"""Юнит-тесты прокси gateway (httpx.MockTransport — без реальной сети).

Проверяется сквозной путь ASGI-запроса: таблица маршрутов → upstream
(метод, путь, query, тело, заголовки) → эхо-ответ (статус, content-type),
плюс обработка неизвестных маршрутов и недоступного сервиса.
"""

import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from services.gateway.main import create_app

TEST_URLS: dict[str, str] = {
    "identity": "http://identity.test",
    "catalog": "http://catalog.test",
    "billing": "http://billing.test",
    "partners": "http://partners.test",
    "dialog": "http://dialog.test",
    "admin": "http://admin.test",
    "chat": "http://chat.test",
}


def _echo_handler(calls: list[dict[str, Any]]):
    """Мок-апстрим: хост = имя сервиса; отвечает эхом."""

    def handler(request: httpx.Request) -> httpx.Response:
        service = request.url.host.split(".")[0]
        query = request.url.query
        calls.append(
            {
                "service": service,
                "method": request.method,
                "path": request.url.path,
                "query": query.decode() if isinstance(query, bytes) else query,
                "body": request.content,
                "internal_token": request.headers.get("x-internal-token"),
                "auth": request.headers.get("authorization"),
            }
        )
        return httpx.Response(
            200,
            json={"ok": True, "service": service},
            headers={"content-type": "application/json"},
        )

    return handler


def _patch_urls(monkeypatch) -> None:
    settings = get_settings()
    for name, url in TEST_URLS.items():
        monkeypatch.setattr(settings, f"svc_{name}_url", url)


@pytest.fixture
def gateway(monkeypatch):
    """(AsyncClient к gateway, список вызовов апстримов) на мок-транспорте."""
    calls: list[dict[str, Any]] = []
    _patch_urls(monkeypatch)
    app = create_app(transport=httpx.MockTransport(_echo_handler(calls)))
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test")
    return http, calls


@pytest.mark.asyncio
async def test_metrics_proxied_to_admin(gateway):
    """GET /metrics (вне /api/*) пробрасывается в admin-домен напрямую."""
    http, calls = gateway
    resp = await http.get("/metrics", headers={"X-Internal-Token": "secret"})
    assert resp.status_code == 200
    assert calls == [
        {
            "service": "admin",
            "method": "GET",
            "path": "/metrics",
            "query": "",
            "body": b"",
            "internal_token": "secret",
            "auth": None,
        }
    ]


@pytest.mark.asyncio
async def test_proxy_forwards_method_path_body_headers(gateway):
    http, calls = gateway
    resp = await http.post(
        "/api/auth/telegram",
        json={"init_data": "abc"},
        headers={"X-Internal-Token": "tok-1"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "identity"}
    assert resp.headers["content-type"].startswith("application/json")

    assert len(calls) == 1
    call = calls[0]
    assert call["service"] == "identity"
    assert call["method"] == "POST"
    assert call["path"] == "/api/auth/telegram"
    assert json.loads(call["body"]) == {"init_data": "abc"}
    # секреты пробрасываются сквозь gateway без изменений
    assert call["internal_token"] == "tok-1"


@pytest.mark.asyncio
async def test_specificity_route_goes_to_billing(gateway):
    """/api/users/{id}/subscription → billing, несмотря на префикс /api/users."""
    http, calls = gateway
    resp = await http.get("/api/users/809464979/subscription")
    assert resp.status_code == 200
    assert calls[0]["service"] == "billing"
    assert calls[0]["path"] == "/api/users/809464979/subscription"


@pytest.mark.asyncio
async def test_query_string_passthrough(gateway):
    http, calls = gateway
    resp = await http.get("/api/listings", params={"make": "BMW", "limit": "10"})
    assert resp.status_code == 200
    assert calls[0]["service"] == "catalog"
    assert calls[0]["query"] == "make=BMW&limit=10"


@pytest.mark.asyncio
async def test_admin_partners_routed_to_partners_service(gateway):
    http, calls = gateway
    resp = await http.post("/api/admin/partners", json={"name": "Партнёр"})
    assert resp.status_code == 200
    assert calls[0]["service"] == "partners"
    assert calls[0]["path"] == "/api/admin/partners"


@pytest.mark.asyncio
async def test_unknown_api_route_404_without_upstream_call(gateway):
    http, calls = gateway
    resp = await http.get("/api/whatever/deep")
    assert resp.status_code == 404
    assert "no route" in resp.json()["detail"]
    assert calls == []  # апстрим не дёргался


@pytest.mark.asyncio
async def test_non_api_path_404(gateway):
    http, calls = gateway
    resp = await http.get("/favicon.ico")
    assert resp.status_code == 404
    assert calls == []


@pytest.mark.asyncio
async def test_upstream_down_returns_502(monkeypatch):
    """Сервис недоступен → 502 с понятным detail (gateway не падает)."""

    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_urls(monkeypatch)
    app = create_app(transport=httpx.MockTransport(failing_handler))
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test")

    resp = await http.get("/api/leads")
    assert resp.status_code == 502
    assert "partners" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_local_health_endpoints_not_proxied(gateway):
    """/health отвечает сам gateway (без вызова апстримов)."""
    http, calls = gateway
    resp = await http.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "gateway"
    assert body["status"] == "ok"
    assert calls == []


@pytest.mark.asyncio
async def test_health_detailed_pings_all_services(monkeypatch):
    """Агрегированный health: все сервисы живы → 200 и полный components."""

    def health_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    _patch_urls(monkeypatch)
    app = create_app(transport=httpx.MockTransport(health_handler))
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test")

    resp = await http.get("/health/detailed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["components"] == {name: True for name in TEST_URLS}

