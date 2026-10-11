"""Тесты admin-эндпоинтов, не требующих БД (ТЗ п. 6.4)."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)


def test_list_adapters_requires_internal_token():
    r = client.get("/api/admin/adapters")
    assert r.status_code == 401


def test_list_adapters():
    r = client.get(
        "/api/admin/adapters",
        headers={"X-Internal-Token": get_settings().app_secret_key},
    )
    assert r.status_code == 200
    assert "EncarApiAdapter" in r.json()["adapters"]


def test_admin_leads_requires_internal_token():
    r = client.get("/api/admin/leads")
    assert r.status_code == 401


def test_admin_leads_empty():
    """GET /api/admin/leads (UI/UX ТЗ §3.6) — пустой список без данных в БД."""
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_user  # noqa: F401
    from app.db.session import get_session
    from conftest import FakeSession

    async def _run():
        app.dependency_overrides[get_session] = lambda: FakeSession()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http:
                return await http.get(
                    "/api/admin/leads",
                    headers={"X-Internal-Token": get_settings().app_secret_key},
                )
        finally:
            app.dependency_overrides.clear()

    import asyncio

    r = asyncio.run(_run())
    assert r.status_code == 200
    assert r.json() == []
