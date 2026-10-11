"""Тесты SQL-дашбордов воронок (P2-бэклог №2): funnel + retention.

SQL не исполняется (FakeSession) — проверяются агрегация/проценты/форма
ответа; сами запросы проверяются на живой БД (см. PROGRESS, «Ожидает»).
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import admin as admin_mod
from app.core.config import get_settings
from app.services import funnels
from tests.conftest import FakeSession


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


async def test_admin_funnel_endpoint():
    session = FakeSession()
    session.set_result([SimpleNamespace(name="search_completed", users=10)])
    r = await admin_mod.analytics_funnel(session, get_settings().app_secret_key, days=14)
    assert r["days"] == 14
    assert r["stages"][0]["users"] == 10


async def test_admin_retention_endpoint_requires_token():
    with pytest.raises(HTTPException) as exc:
        await admin_mod.analytics_retention(FakeSession(), None, days=30)
    assert exc.value.status_code == 401