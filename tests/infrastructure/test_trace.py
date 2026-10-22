"""Тесты сквозного `trace_id` (фаза 1, наблюдаемость)."""

from fastapi.testclient import TestClient

from app.main_api import app

client = TestClient(app)


def test_trace_id_generated_when_absent():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_trace_id_echoed_from_request():
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


def test_trace_id_truncated_to_safe_length():
    response = client.get("/health", headers={"X-Request-ID": "x" * 200})
    assert len(response.headers["X-Request-ID"]) == 64


def test_trace_id_differs_between_requests():
    first = client.get("/health").headers["X-Request-ID"]
    second = client.get("/health").headers["X-Request-ID"]
    assert first != second
