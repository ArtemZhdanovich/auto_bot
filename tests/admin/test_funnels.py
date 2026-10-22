"""Тесты SQL-дашбордов воронок (P2-бэклог №2): funnel + retention.

SQL не исполняется (FakeSession) — проверяются агрегация/проценты/форма
ответа; сами запросы проверяются на живой БД (см. PROGRESS, «Ожидает»).
"""

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.transport.http.routers import admin as admin_mod
from app.main_api import app
from app.infrastructure.services import funnels
from tests.conftest import FakeSession, install_test_container


# ---------- воронка ----------


async def test_funnel_conversions():
    session = FakeSession()
    # порядок строк не важен — сервис мапит по имени события
    session.set_result(
        [
            SimpleNamespace(name="search_completed", users=200),
            SimpleNamespace(name="dialog_message", users=100),
            SimpleNamespace(name="lead_created", users=20),
            SimpleNamespace(name="subscription_activated", users=10),
        ]
    )

    stages = await funnels.funnel(session, days=30)

    assert [s["stage"] for s in stages] == ["search", "dialog", "lead", "subscription"]
    assert [s["users"] for s in stages] == [200, 100, 20, 10]
    assert stages[0]["conv_prev_pct"] == 100.0
    assert stages[1]["conv_prev_pct"] == 50.0  # 100/200
    assert stages[2]["conv_prev_pct"] == 20.0  # 20/100
    assert stages[3]["conv_prev_pct"] == 50.0  # 10/20
    assert stages[3]["conv_first_pct"] == 5.0  # 10/200


async def test_funnel_empty_window():
    session = FakeSession()
    session.set_result([])
    stages = await funnels.funnel(session, days=7)
    assert all(s["users"] == 0 for s in stages)
    assert stages[0]["conv_prev_pct"] == 100.0  # первый этап — база
    assert stages[1]["conv_prev_pct"] == 0.0
    assert stages[0]["conv_first_pct"] == 0.0


async def test_funnel_missing_stage_treated_as_zero():
    session = FakeSession()
    session.set_result([SimpleNamespace(name="search_completed", users=50)])
    stages = await funnels.funnel(session, days=30)
    assert [s["users"] for s in stages] == [50, 0, 0, 0]
    assert stages[1]["conv_prev_pct"] == 0.0


# ---------- retention D1/D7 ----------


async def test_retention_cohorts():
    from datetime import date

    session = FakeSession()
    session.set_result(
        [
            SimpleNamespace(day=date(2026, 10, 10), cohort=100, d1=40, d7=15),
            SimpleNamespace(day=date(2026, 10, 3), cohort=50, d1=25, d7=None),
        ]
    )

    cohorts = await funnels.retention(session, days=30)

    assert cohorts[0] == {
        "day": date(2026, 10, 10),
        "cohort": 100,
        "d1": 40,
        "d7": 15,
        "d1_pct": 40.0,
        "d7_pct": 15.0,
    }
    assert cohorts[1]["d7_pct"] == 0.0  # None (неделя ещё не прошла) → 0
    assert cohorts[1]["d1_pct"] == 50.0


async def test_retention_empty():
    session = FakeSession()
    session.set_result([])
    assert await funnels.retention(session, days=7) == []


# ---------- admin-эндпоинты ----------


@pytest.fixture
async def http_no_db(fake_session: FakeSession):
    """ASGI-клиент без БД: `get_session` → FakeSession.

    Нужен только для проверки прав на уровне роутера (до запросов в БД).
    """

    _prev = app.state.dishka_container
    _container = install_test_container(app, session=fake_session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            yield http
    finally:
        app.state.dishka_container = _prev
        await _container.close()


async def test_admin_funnel_endpoint():
    session = FakeSession()
    session.set_result([SimpleNamespace(name="search_completed", users=10)])
    from app.application.admin.use_cases import GetFunnel
    from app.application.analytics.use_cases import FunnelDashboard
    from app.infrastructure.db.repositories.funnels import (
        SqlAlchemyFunnelRepository,
    )

    use_case = GetFunnel(FunnelDashboard(SqlAlchemyFunnelRepository(session)))
    r = await admin_mod.analytics_funnel(use_case=use_case, days=14)
    assert r["days"] == 14
    assert r["stages"][0]["users"] == 10


async def test_admin_funnel_route_requires_internal_token(http_no_db):
    """Токен проверяется роутером, а не сервисной функцией."""
    resp = await http_no_db.get("/api/admin/analytics/funnel?days=14")
    assert resp.status_code == 401


async def test_admin_funnel_route_with_token(http_no_db, fake_session, internal_token):
    fake_session.set_result([SimpleNamespace(name="search_completed", users=10)])

    resp = await http_no_db.get(
        "/api/admin/analytics/funnel?days=14",
        headers={"X-Internal-Token": internal_token},
    )

    assert resp.status_code == 200
    assert resp.json()["stages"][0]["users"] == 10


async def test_admin_retention_endpoint_returns_cohorts():
    from datetime import date

    session = FakeSession()
    session.set_result(
        [SimpleNamespace(day=date(2026, 10, 10), cohort=4, d1=1, d7=0)]
    )

    from app.application.admin.use_cases import GetRetention
    from app.application.analytics.use_cases import RetentionDashboard
    from app.infrastructure.db.repositories.funnels import (
        SqlAlchemyFunnelRepository,
    )

    use_case = GetRetention(
        RetentionDashboard(SqlAlchemyFunnelRepository(session))
    )
    r = await admin_mod.analytics_retention(use_case=use_case, days=30)

    assert r["days"] == 30
    assert r["cohorts"][0]["cohort"] == 4


async def test_admin_retention_route_requires_internal_token(http_no_db):
    resp = await http_no_db.get("/api/admin/analytics/retention")
    assert resp.status_code == 401