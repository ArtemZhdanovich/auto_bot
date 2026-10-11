"""Тесты audit log админ-действий (UI/UX ТЗ §3.9, миграция 0008)."""

import pytest

from app.services.admin_audit import log_admin_action
from conftest import FakeSession


@pytest.mark.asyncio
async def test_log_admin_action_writes_row():
    session = FakeSession()
    await log_admin_action(
        session,
        action="partner.create",
        entity="partner",
        entity_id="1",
        payload={"name": "Партнёр", "plan": "pro"},
    )
    assert len(session.added) == 1
    row = session.added[0]
    assert row.action == "partner.create"
    assert row.entity == "partner"
    assert row.entity_id == "1"
    assert row.payload["plan"] == "pro"
    assert row.result == "ok"
    assert session.committed is True


@pytest.mark.asyncio
async def test_log_admin_action_never_raises():
    """Ошибка БД при записи audit не ломает основной поток."""

    class BrokenSession(FakeSession):
        async def commit(self):
            raise RuntimeError("db down")

    session = BrokenSession()
    await log_admin_action(session, action="x")  # не должно бросить
