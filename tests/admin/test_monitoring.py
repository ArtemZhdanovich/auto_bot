"""Тесты этапа 8 (мониторинг/алерты): alerting, CB-хуки, /health/detailed, /metrics."""

import asyncio

import pytest
from pydantic import SecretStr

from app.infrastructure.ai.rag_client import RAGClient
from app.config import get_settings
from app.transport.http.deps.internal import internal_token
from app.infrastructure.db.models import SourceHealth
from app.main_api import app
from app.infrastructure.services import alerting, source_health
from conftest import FakeRedis, install_test_container


@pytest.fixture
def alert_env(monkeypatch):
    """Настройки для алертов + стабы Telegram/Redis; возвращает список отправок."""
    settings = get_settings()
    monkeypatch.setattr(settings, "alerts_enabled", True)
    monkeypatch.setattr(settings, "bot_token", SecretStr("test-token"))
    monkeypatch.setattr(settings, "admin_chat_id", 111)
    monkeypatch.setattr(settings, "manager_chat_id", None)
    redis = FakeRedis()
    sent: list[str] = []

    class _FakeNotifier:
        def __init__(self, bot_token: str) -> None:
            self.bot_token = bot_token

        async def send_text(self, chat_id: int, text: str) -> bool:
            sent.append(text)
            return True

    monkeypatch.setattr(alerting, "_get_redis", lambda: redis)
    monkeypatch.setattr(alerting, "_redis", None)
    monkeypatch.setattr(alerting, "TelegramNotifier", _FakeNotifier)
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

    import app.infrastructure.ai.rag_client as rag_mod

    monkeypatch.setattr(rag_mod, "send_alert", _recorder)
    client = RAGClient(base_url="http://rag", api_token=None, enabled=False)
    for _ in range(5):
        client._record_failure_with(threshold=5, cooldown=300)
        await asyncio.sleep(0)
    assert len(alerts) == 1 and "RAG" in alerts[0]


@pytest.mark.asyncio
async def test_health_detailed(monkeypatch):
    async def _ok() -> bool:
        return True

    async def _fail() -> bool:
        return False

    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr("app.main_api._check_db", _ok)
    monkeypatch.setattr("app.main_api._check_redis", _ok)
    monkeypatch.setattr("app.main_api._check_rabbit", _fail)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.get("/health/detailed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["components"] == {"postgres": True, "redis": True, "rabbitmq": False}

    monkeypatch.setattr("app.main_api._check_db", _fail)  # БД критична → 503
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.get("/health/detailed")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_metrics_requires_internal_token(fake_session):
    from httpx import ASGITransport, AsyncClient

    _prev = app.state.dishka_container
    _container = install_test_container(app, session=fake_session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.get("/metrics")
    finally:
        app.state.dishka_container = _prev
        await _container.close()
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_metrics_smoke(fake_session):
    from httpx import ASGITransport, AsyncClient

    HEADERS = {"X-Internal-Token": internal_token()}
    _prev = app.state.dishka_container
    _container = install_test_container(app, session=fake_session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.get("/metrics", headers=HEADERS)
    finally:
        app.state.dishka_container = _prev
        await _container.close()
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "generated_at",
        "users",
        "filters",
        "listings",
        "leads",
        "price_analyses",
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
async def test_alert_fail_open_when_dedup_store_is_down(monkeypatch):
    """Redis недоступен → алерт всё равно уходит (лучше дубль, чем тишина)."""
    sent: list[str] = []

    class _Notifier:
        async def send_text(self, chat_id: int, text: str) -> bool:
            sent.append(text)
            return True

    class _BrokenCache:
        async def get(self, key: str):
            raise ConnectionError("redis down")

        async def set_if_absent(self, key: str, value: str, ttl_sec: int) -> bool:
            raise ConnectionError("redis down")

        async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None:
            raise ConnectionError("redis down")

    from app.application.alerts.use_cases import send_alert as send_alert_use_case

    ok = await send_alert_use_case(
        "redis лежит",
        notifier=_Notifier(),
        chat_id=1,
        cache=_BrokenCache(),
        dedup_key="fail-open",
    )

    assert ok is True
    assert sent == ["redis лежит"]


@pytest.mark.asyncio
async def test_source_health_model_fields():
    """SourceHealth имеет поля, которые читает /metrics и fetch-воркер."""
    health = SourceHealth(source_name="encar")
    assert health.error_streak is None or health.error_streak == 0
