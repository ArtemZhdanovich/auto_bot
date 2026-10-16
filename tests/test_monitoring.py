"""Тесты этапа 8 (мониторинг/алерты): alerting, CB-хуки, /health/detailed, /metrics."""

import asyncio

import pytest

from app.ai.rag_client import RAGClient
from app.core.config import get_settings
from app.core.deps_internal import internal_token
from app.db.models import SourceHealth
from app.db.session import get_session
from app.main import app
from app.services import alerting, source_health
from conftest import FakeRedis


@pytest.fixture
def alert_env(monkeypatch):
    """Настройки для алертов + стабы Telegram/Redis; возвращает список отправок."""
    settings = get_settings()
    monkeypatch.setattr(settings, "alerts_enabled", True)
    monkeypatch.setattr(settings, "bot_token", "test-token")
    monkeypatch.setattr(settings, "admin_chat_id", 111)
    monkeypatch.setattr(settings, "manager_chat_id", None)
    redis = FakeRedis()
    sent: list[str] = []
    monkeypatch.setattr(alerting, "_get_redis", lambda: redis)
    monkeypatch.setattr(alerting, "_redis", None)

    async def _fake_send(bot_token: str, chat_id: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(alerting, "_send_telegram", _fake_send)
    return {"sent": sent, "redis": redis}


@pytest.mark.asyncio
async def test_alert_send_and_dedup(alert_env):
    ok1 = await alerting.send_alert("первый", dedup_key="k1")
    ok2 = await alerting.send_alert("повтор", dedup_key="k1")
    ok3 = await alerting.send_alert("без дедупа")
    assert ok1 is True and ok3 is True
    assert ok2 is False  # повтор подавлен Redis NX
    assert len(alert_env["sent"]) == 2


@pytest.mark.asyncio
async def test_alert_disabled(monkeypatch, alert_env):
    monkeypatch.setattr(get_settings(), "alerts_enabled", False)
    ok = await alerting.send_alert("не уйдёт", dedup_key="k2")
    assert ok is False
    assert alert_env["sent"] == []


@pytest.mark.asyncio
async def test_alert_no_channel(monkeypatch, alert_env):
    monkeypatch.setattr(get_settings(), "admin_chat_id", None)
    ok = await alerting.send_alert("некуда слать", dedup_key="k3")
    assert ok is False


@pytest.mark.asyncio
async def test_source_cb_alert_on_open_and_recovery(monkeypatch, fake_session, alert_env):
    alerts: list[str] = []

    async def _recorder(text: str, **kwargs) -> bool:
        alerts.append(text)
        return True

    monkeypatch.setattr(source_health, "send_alert", _recorder)

    for _ in range(source_health._cb_streak()):
        await source_health.record_error(fake_session, "encar", "boom")
        await asyncio.sleep(0)  # даём fire()-задаче исполниться

    assert len(alerts) == 1 and "encar" in alerts[0]
    # 6-я ошибка — алерта нет (алерт только в момент открытия)
    await source_health.record_error(fake_session, "encar", "boom")
    await asyncio.sleep(0)
    assert len(alerts) == 1

    # восстановление: streak >= порога → recovery-алерт
    await source_health.record_success(
        fake_session, "encar", latency_ms=100, fetched=5
    )
    await asyncio.sleep(0)
    assert len(alerts) == 2 and "восстановился" in alerts[1]


@pytest.mark.asyncio
async def test_should_skip_source_after_threshold(fake_session):
    for _ in range(source_health._cb_streak()):
        await source_health.record_error(fake_session, "kcar", "fail")
    assert await source_health.should_skip_source(fake_session, "kcar") is True
    assert await source_health.should_skip_source(fake_session, "other") is False


@pytest.mark.asyncio
async def test_rag_cb_alert(monkeypatch, alert_env):
    alerts: list[str] = []

    async def _recorder(text: str, **kwargs) -> bool:
        alerts.append(text)
        return True

    import app.ai.rag_client as rag_mod

    monkeypatch.setattr(rag_mod, "send_alert", _recorder)
    client = RAGClient(base_url="http://rag", api_token=None, enabled=False)
    for _ in range(5):
        client._record_failure(fail_threshold=5, cooldown=300)
        await asyncio.sleep(0)
    assert len(alerts) == 1 and "RAG" in alerts[0]


@pytest.mark.asyncio
async def test_health_detailed(monkeypatch):
    async def _ok() -> bool:
        return True

    async def _fail() -> bool:
        return False

    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr("app.main._check_db", _ok)
    monkeypatch.setattr("app.main._check_redis", _ok)
    monkeypatch.setattr("app.main._check_rabbit", _fail)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.get("/health/detailed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["components"] == {"postgres": True, "redis": True, "rabbitmq": False}

    monkeypatch.setattr("app.main._check_db", _fail)  # БД критична → 503
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.get("/health/detailed")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_metrics_requires_internal_token(fake_session):
    from httpx import ASGITransport, AsyncClient

    app.dependency_overrides[get_session] = lambda: fake_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.get("/metrics")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_metrics_smoke(fake_session):
    from httpx import ASGITransport, AsyncClient

    HEADERS = {"X-Internal-Token": internal_token()}
    app.dependency_overrides[get_session] = lambda: fake_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.get("/metrics", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "users",
        "filters",
        "listings",
        "notifications_sent_24h",
        "sources",
        "events_24h",
    }
    assert body["users"]["total"] == 0  # FakeSession: пусто → 0


@pytest.mark.asyncio
async def test_alert_escalation(alert_env):
    """Повторные алерты глушатся экспоненциально дольше: 30 мин → 1 ч → 2 ч."""
    r = alert_env["redis"]
    assert await alerting.send_alert("x", dedup_key="esc") is True
    assert r.ttls["alert:esc"] == 1800
    await r.delete("alert:esc")  # имитируем истечение окна подавления
    assert await alerting.send_alert("x2", dedup_key="esc") is True
    assert r.ttls["alert:esc"] == 3600
    await r.delete("alert:esc")
    assert await alerting.send_alert("x3", dedup_key="esc") is True
    assert r.ttls["alert:esc"] == 7200
    await r.delete("alert:esc")
    assert await alerting.send_alert("x4", dedup_key="esc") is True
    assert r.ttls["alert:esc"] == 7200  # cap 2 ч


@pytest.mark.asyncio
async def test_source_health_model_fields():
    """SourceHealth имеет поля, которые читает /metrics и fetch-воркер."""
    health = SourceHealth(source_name="encar")
    assert health.error_streak is None or health.error_streak == 0
