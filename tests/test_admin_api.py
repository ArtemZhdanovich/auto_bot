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
