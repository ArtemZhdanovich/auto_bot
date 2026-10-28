"""Переговорные чаты до 5 участников (07 §2 + 08 §1): создание, инвайты, лимит.

Сквозные API-тесты на тестовой БД: listing-обязательность (08 §1),
автоген title, код в фейк Redis (TTL 7д), join второго юзера, лимит на
5-м участнике, повторный join, участники в GET /api/chats, партнёрское
зеркало join по X-API-Key и сообщения теми же эндпоинтами.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.db.models import (
    ChatParticipant,
    ChatThread,
    Listing,
    User,
)
from app.infrastructure.security.tokens import create_access_token
from tests.partners.helpers import create_partner

pytestmark = pytest.mark.asyncio

# TTL инвайта: 7 дней (07 §2).
INVITE_TTL_SEC = 7 * 24 * 60 * 60


async def _make_user(
    session: AsyncSession, telegram_id: int, username: str, first_name: str
) -> User:
    """Юзер-участник переговорной в тестовой БД."""
    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


async def _make_lot(session: AsyncSession, owner_id: int | None = None) -> Listing:
    """Активный лот — предмет переговорной (обязателен, 08 §1)."""
    lot = Listing(
        source="market",
        source_id=f"group-{uuid4().hex[:12]}",
        url=f"/listing/market/group-{uuid4().hex[:12]}",
        make="Kia",
        model="Carnival",
        year=2021,
        owner_type="user" if owner_id else None,
        owner_user_id=owner_id,
        parsed_at=datetime.now(UTC),
    )
    session.add(lot)
    await session.flush()
    return lot


def _headers(telegram_id: int) -> dict:
    """Bearer-заголовок юзера."""
    return {
        "Authorization": f"Bearer {create_access_token(telegram_id, get_settings())}"
    }


async def _create_group(
    client,
    headers: dict,
    session: AsyncSession,
    title: str | None = "Поездка на аукцион",
    *,
    listing: Listing | None = None,
) -> str:
    """POST /api/chats/group → thread_id (статус 201 проверяет тест).

    Лот обязателен (08 §1): не передан — создаём свежий owner-лот.
    """
    if listing is None:
        listing = await _make_lot(session)
    body: dict = {"listing_id": listing.id}
    if title is not None:
        body["title"] = title
    resp = await client.post("/api/chats/group", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["thread_id"]


async def _invite(client, headers: dict, thread_id: str) -> str:
    """Код приглашения от участника."""
    resp = await client.post(
        f"/api/chats/{thread_id}/invite", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["invite_code"]


async def _join(client, headers: dict, code: str):
    """POST /api/chats/join → (ответ, thread_id)."""
    resp = await client.post(
        "/api/chats/join", json={"code": code}, headers=headers
    )
    return resp


# =========================================================================
# Создание и валидация
# =========================================================================


async def test_group_created_with_title_and_listing_context(
    client, auth_headers: dict, session: AsyncSession, user
):
    """201; title/listing_id сохранены; без лота/неактивный — 409 (08 §1);
    пустой title — автоген «Переговоры: Make Model Year»."""
    lot = Listing(
        source="market",
        source_id="group-ctx-1",
        url="/listing/market/group-ctx-1",
        make="Kia",
        model="Carnival",
        year=2021,
        owner_type="user",
        owner_user_id=user.telegram_id,
        parsed_at=datetime.now(UTC),
    )
    session.add(lot)
    await session.flush()

    resp = await client.post(
        "/api/chats/group",
        json={"title": "Осмотр Carnival", "listing_id": lot.id},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    thread_id = resp.json()["thread_id"]

    thread = await session.get(ChatThread, UUID(thread_id))
    assert thread is not None
    assert thread.type == "group"
    assert thread.title == "Осмотр Carnival"
    assert thread.listing_id == lot.id
    # Создатель — участник-владелец (role='owner', 08 §2).
    participant = await session.get(
        ChatParticipant, (UUID(thread_id), "user", user.telegram_id)
    )
    assert participant is not None
    assert participant.role == "owner"

    # Пустой title — автогенерация по карточке лота (08 §1).
    autogen = await client.post(
        "/api/chats/group", json={"listing_id": lot.id}, headers=auth_headers
    )
    assert autogen.status_code == 201, autogen.text
    auto_thread = await session.get(
        ChatThread, UUID(autogen.json()["thread_id"])
    )
    assert auto_thread is not None
    assert auto_thread.title == "Переговоры: Kia Carnival 2021"

    # Короткое явное название — 400 (сценарий), пробельное — автоген.
    short = await client.post(
        "/api/chats/group",
        json={"title": "ab", "listing_id": lot.id},
        headers=auth_headers,
    )
    assert short.status_code == 400
    assert "group_title_invalid" in short.json()["detail"]

    # Лот не указан — 409 group_needs_listing (08 §1).
    no_listing = await client.post(
        "/api/chats/group", json={"title": "Без лота"}, headers=auth_headers
    )
    assert no_listing.status_code == 409
    assert "group_needs_listing" in no_listing.json()["detail"]

    # Несуществующий лот — тоже group_needs_listing (409).
    missing = await client.post(
        "/api/chats/group",
        json={"title": "Нормальный титул", "listing_id": 999999999},
        headers=auth_headers,
    )
    assert missing.status_code == 409
    assert "group_needs_listing" in missing.json()["detail"]

    # Лот снят с продажи — 409 listing_not_active.
    lot.is_active = False
    await session.flush()
    inactive = await client.post(
        "/api/chats/group",
        json={"title": "Снятый лот", "listing_id": lot.id},
        headers=auth_headers,
    )
    assert inactive.status_code == 409
    assert "listing_not_active" in inactive.json()["detail"]


async def test_group_requires_registration(client, guest_headers: dict):
    """Гость — 403 (как весь /api/chats)."""
    resp = await client.post(
        "/api/chats/group",
        json={"title": "Гостям нельзя", "listing_id": 1},
        headers=guest_headers,
    )
    assert resp.status_code == 403


# =========================================================================
# Инвайты: код в Redis, join, повторный join
# =========================================================================


async def test_invite_code_stored_in_redis(
    client, auth_headers: dict, session: AsyncSession, fake_redis
):
    """Код — 6 символов base32 без путаницы; ключ chat:invite:{code}
    → thread_id с TTL 7 дней."""
    thread_id = await _create_group(client, auth_headers, session)
    code = await _invite(client, auth_headers, thread_id)

    assert len(code) == 6
    assert all(c in "23456789ABCDEFGHJKMNPQRSTVWXYZ" for c in code)
    key = f"chat:invite:{code}"
    assert fake_redis.data[key] == thread_id
    assert fake_redis.ttls[key] == INVITE_TTL_SEC

    # Не-group тред — 409 (singleton поддержки).
    support = await client.post("/api/chats/support", headers=auth_headers)
    conflict = await client.post(
        f"/api/chats/{support.json()['thread_id']}/invite",
        headers=auth_headers,
    )
    assert conflict.status_code == 409
    assert "not_group_thread" in conflict.json()["detail"]

    # Чужой (не участник) не выпускает код — 404.
    outsider = await _make_user(session, 910_301, "outsider", "Посторонний")
    denied = await client.post(
        f"/api/chats/{thread_id}/invite",
        headers=_headers(outsider.telegram_id),
    )
    assert denied.status_code == 404


async def test_join_second_user_and_rejoin_creator(
    client, auth_headers: dict, session: AsyncSession, fake_redis, user
):
    """Второй юзер — 201; повторный join и создатель — 200 тот же тред."""
    second = await _make_user(session, 910_002, "second", "Второй")
    thread_id = await _create_group(client, auth_headers, session)
    code = await _invite(client, auth_headers, thread_id)

    joined = await _join(client, _headers(second.telegram_id), code)
    assert joined.status_code == 201, joined.text
    assert joined.json()["thread_id"] == thread_id

    # Повторный join того же юзера — 200 без добавления строки.
    again = await _join(client, _headers(second.telegram_id), code)
    assert again.status_code == 200
    assert again.json()["thread_id"] == thread_id

    # Создатель «заходит» по коду — 200 тот же тред.
    creator = await _join(client, auth_headers, code)
    assert creator.status_code == 200
    assert creator.json()["thread_id"] == thread_id

    # Вошедший видит переговорную в своём списке чатов (по участию).
    second_threads = (
        await client.get("/api/chats", headers=_headers(second.telegram_id))
    ).json()
    mine = next(t for t in second_threads if t["id"] == thread_id)
    assert mine["is_group"] is True

    # Несуществующий/истёкший код — 404.
    missing = await _join(client, _headers(second.telegram_id), "ZZZZZZ")
    assert missing.status_code == 404
    assert "invite_not_found" in missing.json()["detail"]


# =========================================================================
# Лимит 5 участников INCLUDING создателя
# =========================================================================


async def test_group_full_at_five_participants(
    client, auth_headers: dict, session: AsyncSession, fake_redis
):
    """Создатель + 4 join = 5 участников; шестой — 409 group_is_full."""
    others = [
        await _make_user(session, 910_101 + i, f"u{i}", f"Юзер {i}")
        for i in range(5)
    ]
    thread_id = await _create_group(client, auth_headers, session)
    code = await _invite(client, auth_headers, thread_id)

    for i, member in enumerate(others[:4]):
        resp = await _join(client, _headers(member.telegram_id), code)
        assert resp.status_code == 201, f"участник {i}: {resp.text}"

    # Шестое место занято (создатель + 4) — 409.
    full = await _join(client, _headers(others[4].telegram_id), code)
    assert full.status_code == 409
    assert "group_is_full" in full.json()["detail"]


# =========================================================================
# Список чатов: is_group, участники с именами
# =========================================================================


async def test_group_in_list_with_participants_and_names(
    client, auth_headers: dict, session: AsyncSession, fake_redis, user
):
    """GET /api/chats: group-тред с title, is_group, participants (name)."""
    second = await _make_user(session, 910_201, "masha", "Мария")
    thread_id = await _create_group(
        client, auth_headers, session, title="Моя переговорная"
    )
    code = await _invite(client, auth_headers, thread_id)
    assert (
        await _join(client, _headers(second.telegram_id), code)
    ).status_code == 201

    threads = (await client.get("/api/chats", headers=auth_headers)).json()
    mine = next(t for t in threads if t["id"] == thread_id)
    assert mine["type"] == "group"
    assert mine["is_group"] is True
    assert mine["title"] == "Моя переговорная"
    participants = {(p["user_type"], p["user_id"]): p["name"] for p in mine["participants"]}
    assert set(participants) == {
        ("user", user.telegram_id),
        ("user", second.telegram_id),
    }
    assert participants[("user", second.telegram_id)] == "Мария"

    # Обычные треды не получают participants (service-типы).
    await client.post("/api/chats/support", headers=auth_headers)
    threads = (await client.get("/api/chats", headers=auth_headers)).json()
    support = next(t for t in threads if t["type"] == "support")
    assert support["is_group"] is False
    assert support["participants"] == []


# =========================================================================
# Партнёрское зеркало: join по X-API-Key, тред в кабинете, сообщения
# =========================================================================


async def test_partner_join_by_code_and_messages(
    client, auth_headers: dict, session: AsyncSession, fake_redis, internal_token: str
):
    """Партнёр входит по коду, видит тред в кабинете и пишет в него."""
    account, raw_key = await create_partner(
        session,
        name="Автосалон Приморский",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100"),
        cpl_rate=None,
    )
    partner_headers = {"X-API-Key": raw_key}
    thread_id = await _create_group(client, auth_headers, session)
    code = await _invite(client, auth_headers, thread_id)

    joined = await client.post(
        "/api/partner/chats/join", json={"code": code}, headers=partner_headers
    )
    assert joined.status_code == 201, joined.text
    assert joined.json()["thread_id"] == thread_id

    # Повторный join партнёром — 200.
    again = await client.post(
        "/api/partner/chats/join", json={"code": code}, headers=partner_headers
    )
    assert again.status_code == 200

    # Кабинет партнёра видит group-тред с собой в участниках.
    partner_threads = (
        await client.get("/api/partner/chats", headers=partner_headers)
    ).json()
    mine = next(t for t in partner_threads if t["id"] == thread_id)
    assert mine["is_group"] is True
    partner_row = next(
        p for p in mine["participants"] if p["user_type"] == "partner"
    )
    assert partner_row["user_id"] == account.id
    assert partner_row["name"] == "Автосалон Приморский"

    # Сообщения: юзер — обычным эндпоинтом, партнёр — зеркалом.
    sent = await client.post(
        f"/api/chats/{thread_id}/messages",
        json={"body": " Осмотр в 15:00?"},
        headers=auth_headers,
    )
    assert sent.status_code == 201, sent.text
    reply = await client.post(
        f"/api/partner/chats/{thread_id}/messages",
        json={"body": "Подтверждаю"},
        headers=partner_headers,
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["sender_type"] == "partner"

    # Оба читают историю теми же эндпоинтами (тело trim'ится сценарием);
    # первой лежит system-строка с правилами комнаты (08 §3).
    history = (
        await client.get(
            f"/api/chats/{thread_id}/messages", headers=auth_headers
        )
    ).json()
    bodies = [m["body"] for m in history]
    assert bodies[0].startswith("Правила комнаты")
    assert bodies[1:] == ["Осмотр в 15:00?", "Подтверждаю"]

    # Subscribe-proxy пускает вошедшего партнёра (каналы participant-based).
    proxy = await client.post(
        "/api/chats/subscribe-proxy",
        json={
            "client": "c-1",
            "channel": f"thread:{thread_id}",
            "user": f"partner:{account.id}",
        },
        headers={"X-Internal-Token": internal_token},
    )
    assert proxy.status_code == 200
    assert proxy.json() == {}

    # Чужой партнёр тред не видит и в кабинет он не попадает.
    _, other_raw = await create_partner(
        session,
        name="Соседний салон",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100"),
        cpl_rate=None,
    )
    other_threads = (
        await client.get("/api/partner/chats", headers={"X-API-Key": other_raw})
    ).json()
    assert all(t["id"] != thread_id for t in other_threads)
    foreign = await client.get(
        f"/api/partner/chats/{thread_id}/messages",
        headers={"X-API-Key": other_raw},
    )
    assert foreign.status_code == 404
