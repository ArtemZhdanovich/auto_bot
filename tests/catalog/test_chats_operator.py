"""Операторский контур чатов (§2/§6): зов оператора + /api/admin/chats.

- POST /api/chats/{id}/summon-operator: пауза бота + system-строка,
  идемпотентность, гость — 403, не-support — 409, чужой тред — 404;
- GET /api/admin/chats (X-Internal-Token): список support-тредов с
  фильтром paused, сниппетом последнего юзер-сообщения и именем юзера;
- POST /api/admin/chats/{id}/messages: sender_type='operator', флаг
  bot_paused в БД (repo) и снятие флага сообщением юзера;
- GET /api/admin/chats/unread: бейдж «ждут ответа» для поллинга админки.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.chat.use_cases import SUMMON_OPERATOR_MESSAGE
from app.infrastructure.db.models import ChatMessage, ChatThread, User

pytestmark = pytest.mark.asyncio


async def _support_thread_of(
    session: AsyncSession, user_id: int, *, bot_paused: bool = False
) -> UUID:
    """Вставить тред напрямую (без REST — для чужих владельцев/типов)."""
    thread = ChatThread(type="support", user_id=user_id, bot_paused=bot_paused)
    session.add(thread)
    await session.flush()
    return thread.id


async def _say(
    session: AsyncSession,
    thread_id: UUID,
    sender_type: str,
    body: str,
    sender_id: int | None = None,
) -> None:
    """Вставить сообщение треда напрямую (минуя REST и спавнер бота)."""
    session.add(
        ChatMessage(
            thread_id=thread_id,
            sender_type=sender_type,
            sender_id=sender_id,
            body=body,
        )
    )
    await session.flush()


async def _bot_paused(session: AsyncSession, thread_id: UUID) -> bool:
    """Флаг bot_paused треда из БД (постусловие репозитория, §2)."""
    thread = (
        await session.execute(
            select(ChatThread).where(ChatThread.id == thread_id)
        )
    ).scalar_one()
    return bool(thread.bot_paused)


# =========================================================================
# POST /api/chats/{id}/summon-operator (юзер, JWT)
# =========================================================================


async def test_summon_operator_pauses_bot_and_writes_system(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    """Зов: bot_paused=True в БД + system-строка, видная юзеру в ленте."""
    created = (
        await client.post("/api/chats/support", headers=auth_headers)
    ).json()
    thread_id = created["thread_id"]

    resp = await client.post(
        f"/api/chats/{thread_id}/summon-operator", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot_paused"] is True
    assert body["message"]["sender_type"] == "system"
    assert body["message"]["body"] == SUMMON_OPERATOR_MESSAGE

    assert await _bot_paused(session, UUID(thread_id)) is True
    messages = (
        await client.get(
            f"/api/chats/{thread_id}/messages", headers=auth_headers
        )
    ).json()
    assert any(
        m["sender_type"] == "system" and m["body"] == SUMMON_OPERATOR_MESSAGE
        for m in messages
    )


async def test_summon_operator_idempotent_while_paused(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    """Повторный зов при уже стоящей паузе — 200 без второй system-строки."""
    created = (
        await client.post("/api/chats/support", headers=auth_headers)
    ).json()
    thread_id = UUID(created["thread_id"])

    first = await client.post(
        f"/api/chats/{thread_id}/summon-operator", headers=auth_headers
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/chats/{thread_id}/summon-operator", headers=auth_headers
    )
    assert second.status_code == 200
    assert second.json()["message"] is None  # новой записи не было

    summons = (
        await session.execute(
            select(ChatMessage).where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.body == SUMMON_OPERATOR_MESSAGE,
            )
        )
    ).scalars().all()
    assert len(summons) == 1


async def test_summon_operator_guest_forbidden(
    client: AsyncClient, guest_headers: dict
):
    """Гость (синтетический id < 0) — 403, как весь /api/chats (06 §5)."""
    resp = await client.post(
        f"/api/chats/{uuid4()}/summon-operator", headers=guest_headers
    )
    assert resp.status_code == 403
    assert "chats_registration_required" in resp.json()["detail"]


async def test_summon_operator_not_support_conflict(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, user
):
    """Seller-тред — 409: оператор зовётся только в поддержке (§6)."""
    thread_id = await _support_thread_of(session, user.telegram_id)
    await session.execute(
        update(ChatThread)
        .where(ChatThread.id == thread_id)
        .values(type="seller", listing_id=777)
    )
    await session.flush()

    resp = await client.post(
        f"/api/chats/{thread_id}/summon-operator", headers=auth_headers
    )
    assert resp.status_code == 409
    assert "not_support_thread" in resp.json()["detail"]


async def test_summon_operator_foreign_thread_not_found(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    """Чужой support-тред — 404 (правила доступа те же, что у сообщений)."""
    foreign = await _support_thread_of(session, 915555)
    resp = await client.post(
        f"/api/chats/{foreign}/summon-operator", headers=auth_headers
    )
    assert resp.status_code == 404


# =========================================================================
# /api/admin/chats (X-Internal-Token)
# =========================================================================


async def test_admin_chats_require_internal_token(client: AsyncClient):
    """Без X-Internal-Token весь операторский контур закрыт (401)."""
    for path in (
        "/api/admin/chats",
        "/api/admin/chats/unread",
        f"/api/admin/chats/{uuid4()}/messages",
    ):
        resp = await client.get(path)
        assert resp.status_code == 401, path
    post = await client.post(
        f"/api/admin/chats/{uuid4()}/messages", json={"body": "привет"}
    )
    assert post.status_code == 401


async def test_admin_list_chats_paused_filter_and_fields(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    internal_token: str,
):
    """Список: имя юзера, время/сниппет последнего юзер-сообщения,
    фильтр paused=true отдаёт только треды с паузой бота."""
    headers = {"X-Internal-Token": internal_token}
    mine = (
        (await client.post("/api/chats/support", headers=auth_headers)).json()
    )["thread_id"]

    # Второй юзер: тред с последним юзер-сообщением и паузой бота
    # (пауза = юзер позвал оператора / оператор вклинился, §6).
    session.add(User(telegram_id=910001, username="anna", is_active=True))
    other = await _support_thread_of(session, 910001)
    await _say(session, other, "user", "Помогите с *доставкой* в Казань", 910001)
    await session.execute(
        update(ChatThread).where(ChatThread.id == other).values(bot_paused=True)
    )
    await session.flush()

    all_threads = (
        await client.get("/api/admin/chats", headers=headers)
    ).json()
    assert {t["id"] for t in all_threads} == {mine, str(other)}

    paused = (
        await client.get(
            "/api/admin/chats",
            params={"type": "support", "paused": True},
            headers=headers,
        )
    ).json()
    assert [t["id"] for t in paused] == [str(other)]
    row = paused[0]
    assert row["bot_paused"] is True
    assert row["user_id"] == 910001
    assert row["user_name"] == "anna"
    # markdown-разметка срезана в сниппете (plain_text)
    assert row["last_user_message_snippet"] == "Помогите с доставкой в Казань"
    assert row["last_user_message_at"] is not None

    # Мой тред (без паузы) фильтром не отдаётся
    unpaused = (
        await client.get(
            "/api/admin/chats", params={"paused": False}, headers=headers
        )
    ).json()
    assert [t["id"] for t in unpaused] == [mine]


async def test_admin_messages_operator_sees_all(
    client: AsyncClient, auth_headers: dict, internal_token: str,
    session: AsyncSession,
):
    """Оператору видна вся лента (system/юзер/бот) по возрастанию id."""
    created = (
        await client.post("/api/chats/support", headers=auth_headers)
    ).json()
    thread_id = UUID(created["thread_id"])
    await _say(session, thread_id, "user", "вопрос", 900001)
    await _say(session, thread_id, "bot", "ответ")

    resp = await client.get(
        f"/api/admin/chats/{thread_id}/messages",
        headers={"X-Internal-Token": internal_token},
    )
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()]
    assert ids == sorted(ids)
    assert {m["sender_type"] for m in resp.json()} >= {"system", "user", "bot"}

    missing = await client.get(
        f"/api/admin/chats/{uuid4()}/messages",
        headers={"X-Internal-Token": internal_token},
    )
    assert missing.status_code == 404


async def test_admin_send_message_sets_bot_paused_in_db(
    client: AsyncClient,
    auth_headers: dict,
    internal_token: str,
    session: AsyncSession,
):
    """Ответ оператора: sender_type='operator', флаг паузы в БД; сообщение
    юзера снимает паузу (репозиторий, §2/§6)."""
    created = (
        await client.post("/api/chats/support", headers=auth_headers)
    ).json()
    thread_id = UUID(created["thread_id"])

    resp = await client.post(
        f"/api/admin/chats/{thread_id}/messages",
        json={"body": "Здравствуйте! Оператор на связи"},
        headers={"X-Internal-Token": internal_token},
    )
    assert resp.status_code == 201
    assert resp.json()["sender_type"] == "operator"
    assert await _bot_paused(session, thread_id) is True

    # Юзер пишет → репозиторий снимает паузу (бот снова отвечает, §6)
    user_reply = await client.post(
        f"/api/chats/{thread_id}/messages",
        json={"body": "спасибо, вопрос снят"},
        headers=auth_headers,
    )
    assert user_reply.status_code == 201
    assert await _bot_paused(session, thread_id) is False

    # Пробельное тело — 400 empty_message (общий формат сообщений)
    bad = await client.post(
        f"/api/admin/chats/{thread_id}/messages",
        json={"body": "  "},
        headers={"X-Internal-Token": internal_token},
    )
    assert bad.status_code == 400
    assert "empty_message" in bad.json()["detail"]


async def test_admin_unread_count(
    client: AsyncClient,
    auth_headers: dict,
    internal_token: str,
    session: AsyncSession,
):
    """Unread: юзер писал позже оператора (или операторов не было) — 1;
    ответ оператора гасит счётчик."""
    headers = {"X-Internal-Token": internal_token}
    created = (
        await client.post("/api/chats/support", headers=auth_headers)
    ).json()
    thread_id = UUID(created["thread_id"])

    async def unread() -> int:
        resp = await client.get("/api/admin/chats/unread", headers=headers)
        return resp.json()["threads"]

    # Юзер задал вопрос, операторов не было — тред ждёт ответа
    await _say(session, thread_id, "user", "когда привезут?", 900001)
    assert await unread() == 1

    # Оператор ответил — счётчик погас
    await _say(session, thread_id, "operator", "На следующей неделе", 0)
    assert await unread() == 0

    # Юзер переспросил после ответа — снова ждёт
    await _say(session, thread_id, "user", "а точнее?", 900001)
    assert await unread() == 1
