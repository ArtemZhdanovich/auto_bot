"""Выжимка диалога для менеджера/партнёра (12 §3): admin + partner + кэш.

Admin-контур — X-Internal-Token (видит все треды), партнёрский — X-API-Key
с проверкой участия (чужой тред — 404, как у partner messages). Выжимка
кэшируется в Redis на 1 час по последнему сообщению.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import ChatMessage, ChatThread
from tests.partners.helpers import create_partner

pytestmark = pytest.mark.asyncio

SUMMARY_TEXT = (
    "Клиент ищет Kia Carnival до $32k, смотрел лот 5. Сомневается в "
    "состоянии кузова после ДТП. Договорились прислать чек осмотра: "
    "следующий шаг — выжимка истории лота."
)


class _FakeLLM:
    async def complete(self, messages):
        return SimpleNamespace(text=SUMMARY_TEXT, tool_calls=[])


class _FailingLLM:
    async def complete(self, messages):
        raise RuntimeError("llm down")


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: _FakeLLM()
    )


@pytest.fixture
def internal_headers(internal_token) -> dict[str, str]:
    """Заголовки с internal-токеном (значение из настроек приложения)."""
    return {"X-Internal-Token": internal_token}


async def _thread_with_dialog(
    session: AsyncSession, *, partner_id: int | None = None, user_id: int
) -> tuple[UUID, int]:
    """Тред (support/seller) с парой сообщений; (thread_id, last_msg_id)."""
    thread = ChatThread(
        type="seller" if partner_id is not None else "support",
        user_id=user_id,
        partner_id=partner_id,
    )
    session.add(thread)
    await session.flush()
    for sender_type, sender_id, body in (
        ("user", user_id, "Ищу Carnival до $32k, что с кузовом?"),
        ("partner", partner_id, "Пришлю чек осмотра, там видно зоны."),
    ):
        session.add(
            ChatMessage(
                thread_id=thread.id,
                sender_type=sender_type,
                sender_id=sender_id,
                body=body,
            )
        )
    await session.flush()
    last_id = (
        await session.execute(
            select(ChatMessage.id)
            .where(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.id.desc())
            .limit(1)
        )
    ).scalar_one()
    return thread.id, last_id


async def test_admin_summary_200(
    client: AsyncClient,
    internal_headers: dict,
    session: AsyncSession,
    user,
    fake_llm,
):
    """POST /api/admin/chats/{id}/summary → выжимка ≤600 знаков (12 §3)."""
    thread_id, last_id = await _thread_with_dialog(
        session, user_id=user.telegram_id
    )

    resp = await client.post(
        f"/api/admin/chats/{thread_id}/summary", headers=internal_headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["thread_id"] == str(thread_id)
    assert data["summary"] == SUMMARY_TEXT
    assert data["last_message_id"] == last_id
    assert data["generated_at"]


async def test_admin_summary_cached(
    client: AsyncClient,
    internal_headers: dict,
    session: AsyncSession,
    user,
    fake_llm,
    monkeypatch,
):
    """Повторный запрос берёт выжимку из кэша (LLM не нужен)."""
    thread_id, _ = await _thread_with_dialog(
        session, user_id=user.telegram_id
    )

    first = await client.post(
        f"/api/admin/chats/{thread_id}/summary", headers=internal_headers
    )
    assert first.status_code == 200

    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: _FailingLLM()
    )
    second = await client.post(
        f"/api/admin/chats/{thread_id}/summary", headers=internal_headers
    )
    assert second.status_code == 200
    assert second.json()["summary"] == SUMMARY_TEXT


async def test_partner_summary_200_and_foreign_404(
    client: AsyncClient,
    session: AsyncSession,
    user,
    fake_llm,
):
    """Партнёр видит выжимку своего треда; чужой — 404 (12 §3)."""
    from decimal import Decimal

    account, raw = await create_partner(
        session,
        name="Салон",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100.00"),
        cpl_rate=None,
    )
    other, raw_other = await create_partner(
        session,
        name="Другой салон",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100.00"),
        cpl_rate=None,
    )
    thread_id, last_id = await _thread_with_dialog(
        session, partner_id=account.id, user_id=user.telegram_id
    )

    resp = await client.post(
        f"/api/partner/chats/{thread_id}/summary", headers={"X-API-Key": raw}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["summary"] == SUMMARY_TEXT
    assert data["last_message_id"] == last_id

    foreign = await client.post(
        f"/api/partner/chats/{thread_id}/summary",
        headers={"X-API-Key": raw_other},
    )
    assert foreign.status_code == 404


async def test_admin_summary_404_unknown_thread(
    client: AsyncClient, internal_headers: dict, fake_llm
):
    """Несуществующий тред — 404 (кэш и LLM не участвуют)."""
    from uuid import uuid4

    resp = await client.post(
        f"/api/admin/chats/{uuid4()}/summary", headers=internal_headers
    )
    assert resp.status_code == 404
