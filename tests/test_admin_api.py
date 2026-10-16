"""Тесты admin-эндпоинтов, не требующих БД (ТЗ п. 6.4)."""

from fastapi.testclient import TestClient

from app.core.deps_internal import internal_token
from app.main import app

client = TestClient(app)


def test_list_adapters_requires_internal_token():
    r = client.get("/api/admin/adapters")
    assert r.status_code == 401


def test_list_adapters():
    r = client.get(
        "/api/admin/adapters",
        headers={"X-Internal-Token": internal_token()},
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
                    headers={"X-Internal-Token": internal_token()},
                )
        finally:
            app.dependency_overrides.clear()

    import asyncio

    r = asyncio.run(_run())
    assert r.status_code == 200
    assert r.json() == []


def test_upsert_source_merges_config():
    """PUT /sources/{name} ДОЛАВНЯЕТ config/capabilities, а не затирает их.

    Регрессия ParserDoctor: mvp.sh при каждом старте перезаписывал config
    значением {"page_size": 20}, уничтожая repaired-селекторы и doctor_mode.
    """
    from httpx import ASGITransport, AsyncClient

    from app.db.models import Source
    from app.db.session import get_session
    from conftest import FakeSession

    existing = Source(
        name="encar",
        adapter_class="EncarApiAdapter",
        kind="api",
        enabled=True,
        priority=100,
        retry_policy={"max": 3, "backoff": "exponential"},
        capabilities={"country": "KR", "price_currency": "KRW"},
        config={
            "page_size": 20,
            "selector_card": "div.list-item",  # восстановлен доктором
            "doctor_mode": True,
        },
    )
    session = FakeSession()
    session.add(existing)

    async def _run():
        app.dependency_overrides[get_session] = lambda: session
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http:
                return await http.put(
                    "/api/admin/sources/encar",
                    headers={"X-Internal-Token": internal_token()},
                    json={
                        "name": "encar",
                        "adapter_class": "EncarApiAdapter",
                        "kind": "api",
                        "enabled": True,
                        "priority": 100,
                        "config": {"page_size": 20},
                    },
                )
        finally:
            app.dependency_overrides.clear()

    import asyncio

    r = asyncio.run(_run())
    assert r.status_code == 200, r.text
    body = r.json()
    # merge: ключи источника сохранены, body перекрыл page_size
    assert body["config"] == {
        "page_size": 20,
        "selector_card": "div.list-item",
        "doctor_mode": True,
    }
    assert body["capabilities"] == {"country": "KR", "price_currency": "KRW"}
    assert body["retry_policy"] == {"max": 3, "backoff": "exponential"}
    assert session.committed is True


def test_upsert_source_new_creates_with_body_config():
    """Новый источник: config берётся из body как есть."""
    from httpx import ASGITransport, AsyncClient

    from app.db.session import get_session
    from conftest import FakeSession

    session = FakeSession()

    async def _run():
        app.dependency_overrides[get_session] = lambda: session
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http:
                return await http.put(
                    "/api/admin/sources/kcar",
                    headers={"X-Internal-Token": internal_token()},
                    json={
                        "name": "kcar",
                        "adapter_class": "KCarApiAdapter",
                        "kind": "api",
                        "config": {"page_size": 100},
                    },
                )
        finally:
            app.dependency_overrides.clear()

    import asyncio

    r = asyncio.run(_run())
    assert r.status_code == 200, r.text
    assert r.json()["config"] == {"page_size": 100}
    assert session.committed is True


def test_source_config_null_db_fields_to_defaults():
    """NULL retry_policy/capabilities/config в БД не роняют GET /sources (500)."""
    from app.db.models import Source
    from app.schemas.criteria import SourceConfig

    row = Source(
        name="legacy",
        adapter_class="KCarApiAdapter",
        kind="api",
        enabled=True,
        priority=100,
        rate_limit_per_min=60,
        max_concurrency=3,
        timeout_sec=30,
        retry_policy=None,
        capabilities=None,
        config=None,
    )
    cfg = SourceConfig.model_validate(row, from_attributes=True)
    assert cfg.retry_policy == {"max": 3, "backoff": "exponential"}
    assert cfg.capabilities == {}
    assert cfg.config == {}
