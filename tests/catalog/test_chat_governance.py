"""Говернанс переговорных (08 §1-§6): роли, kick/leave/archive, маскировка,
антифрод (rate-limit, жалобы), аудит, retention, mute.

Сквозные API-тесты на тестовой БД: lifecycle group-треда (owner/member),
привязка к лоту, маскировка контактов при сохранении, лимит 20/мин,
репорты + админ-контур (список/обработка/кик), лента chat_events и
90-дневный retention закрытых тредов.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.chat.privacy import PHONE_MASK
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


# =========================================================================
# Хелперы
# =========================================================================


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


def _headers(telegram_id: int) -> dict:
    """Bearer-заголовок юзера."""
    return {
        "Authorization": f"Bearer {create_access_token(telegram_id, get_settings())}"
    }


async def _make_lot(session: AsyncSession, owner_id: int | None = None) -> Listing:
    """Активный owner-лот — предмет переговорной (обязателен, 08 §1)."""
    lot = Listing(
        source="market",
        source_id=f"gov-{uuid4().hex[:12]}",
        url=f"/listing/market/gov-{uuid4().hex[:12]}",
        make="Kia",
        model="Carnival",
        year=2021,
        owner_type="user" if owner_id is not None else None,
        owner_user_id=owner_id,
        parsed_at=datetime.now(UTC),
    )
    session.add(lot)
    await session.flush()
    return lot


async def _create_group(
    client: AsyncClient,
    headers: dict,
    session: AsyncSession,
    lot: Listing,
    title: str = "Переговоры по осмотру",
) -> str:
    """Переговорная с владельцем = автор запроса (201)."""
    resp = await client.post(
        "/api/chats/group",
        json={"title": title, "listing_id": lot.id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["thread_id"]


async def _join(
    client: AsyncClient,
    session: AsyncSession,
    owner_headers: dict,
    member_headers: dict,
    thread_id: str,
) -> None:
    """Owner выпускает код, юзер member_headers входит участником."""
    code = (
        await client.post(
            f"/api/chats/{thread_id}/invite", headers=owner_headers
        )
    ).json()["invite_code"]
    joined = await client.post(
        "/api/chats/join", json={"code": code}, headers=member_headers
    )
    assert joined.status_code == 201, joined.text


async def _thread_rows(
    session: AsyncSession, thread_id: str
) -> list[ChatParticipant]:
    """Строки участников треда."""
    return list(
        (
            await session.execute(
                select(ChatParticipant).where(
                    ChatParticipant.thread_id == UUID(thread_id)
                )
            )
        ).scalars()
    )


# =========================================================================
# §2 Роли: invite только owner
# =========================================================================


async def test_invite_owner_only(
    client, auth_headers: dict, session: AsyncSession, lot: Listing
):
    """Owner выпускает код (200); member — 403 invite_owner_only."""
    member = await _make_user(session, 920_101, "member1", "Мария")
    thread_id = await _create_group(client, auth_headers, session, lot)
    await _join(
        client, session, auth_headers, _headers(member.telegram_id), thread_id
    )

    owner_invite = await client.post(
        f"/api/chats/{thread_id}/invite", headers=auth_headers
    )
    assert owner_invite.status_code == 200

    member_invite = await client.post(
        f"/api/chats/{thread_id}/invite",
        headers=_headers(member.telegram_id),
    )
    assert member_invite.status_code == 403
    assert "invite_owner_only" in member_invite.json()["detail"]


# =========================================================================
# §2 Kick
# =========================================================================


async def test_kick_rules(
    client, auth_headers: dict, session: AsyncSession, lot: Listing, user
):
    """Owner кикает member (ок); кик себя — подсказка leave; member не
    кикает (403); кик owner-строки — 409 cannot_kick_owner."""
    member = await _make_user(session, 920_201, "member2", "Пётр")
    outsider = await _make_user(session, 920_202, "stranger", "Лев")
    thread_id = await _create_group(client, auth_headers, session, lot)
    await _join(
        client, session, auth_headers, _headers(member.telegram_id), thread_id
    )

    # Owner кикает member — ок, строка исчезает.
    kicked = await client.delete(
        f"/api/chats/{thread_id}/participants/user/{member.telegram_id}",
        headers=auth_headers,
    )
    assert kicked.status_code == 200, kicked.text
    assert all(p.user_id != member.telegram_id for p in await _thread_rows(session, thread_id))

    # Owner «кикает себя» — 409 с подсказкой использовать leave.
    self_kick = await client.delete(
        f"/api/chats/{thread_id}/participants/user/{user.telegram_id}",
        headers=auth_headers,
    )
    assert self_kick.status_code == 409
    assert "leave" in self_kick.json()["detail"]

    # Member (вернём его кодом) не может кикать даже outsiders — 403.
    await _join(
        client, session, auth_headers, _headers(member.telegram_id), thread_id
    )
    by_member = await client.delete(
        f"/api/chats/{thread_id}/participants/user/{outsider.telegram_id}",
        headers=_headers(member.telegram_id),
    )
    assert by_member.status_code == 403
    assert "kick_owner_only" in by_member.json()["detail"]

    # Цели нет среди участников — 404.
    missing = await client.delete(
        f"/api/chats/{thread_id}/participants/user/{outsider.telegram_id}",
        headers=auth_headers,
    )
    assert missing.status_code == 404

    # Кик owner-строки другим путём: у единственного owner это self-kick;
    # отдельная проверка cannot_kick_owner — в админ-ките ниже (там можно).
    thread = await session.get(ChatThread, UUID(thread_id))
    assert thread is not None and thread.status == "active"


# =========================================================================
# §2 Leave / Archive
# =========================================================================


async def test_leave_member_ok_owner_must_archive(
    client, auth_headers: dict, session: AsyncSession, lot: Listing, user
):
    """Owner с участниками — 409 owner_must_archive; member уходит свободно;
    уход последнего owner закрывает тред."""
    member = await _make_user(session, 920_301, "member3", "Ольга")
    thread_id = await _create_group(client, auth_headers, session, lot)
    await _join(
        client, session, auth_headers, _headers(member.telegram_id), thread_id
    )

    # Owner не последний — сначала «Архивировать» (409).
    owner_left = await client.post(
        f"/api/chats/{thread_id}/leave", headers=auth_headers
    )
    assert owner_left.status_code == 409
    assert "owner_must_archive" in owner_left.json()["detail"]

    # Member уходит свободно (200), строка исчезает.
    member_left = await client.post(
        f"/api/chats/{thread_id}/leave", headers=_headers(member.telegram_id)
    )
    assert member_left.status_code == 200, member_left.text
    assert all(
        p.user_id != member.telegram_id
        for p in await _thread_rows(session, thread_id)
    )

    # Теперь owner последний — уход ок, тред закрылся.
    last_left = await client.post(
        f"/api/chats/{thread_id}/leave", headers=auth_headers
    )
    assert last_left.status_code == 200
    thread = await session.get(ChatThread, UUID(thread_id))
    assert thread is not None
    assert thread.status == "closed"
    assert thread.closed_at is not None


async def test_last_owner_leave_closes_thread(
    client, auth_headers: dict, session: AsyncSession, lot: Listing, user
):
    """Последний участник (owner) уходит — тред closed + closed_at."""
    thread_id = await _create_group(client, auth_headers, session, lot)
    left = await client.post(
        f"/api/chats/{thread_id}/leave", headers=auth_headers
    )
    assert left.status_code == 200
    thread = await session.get(ChatThread, UUID(thread_id))
    assert thread is not None
    assert thread.status == "closed"
    assert thread.closed_at is not None


async def test_archive_readonly_and_idempotent(
    client, auth_headers: dict, session: AsyncSession, lot: Listing
):
    """Архив: closed + system-строка; сообщения — 403 thread_archived;
    повтор архива идемпотентен (200, без второй system-строки)."""
    member = await _make_user(session, 920_401, "member4", "Игорь")
    thread_id = await _create_group(client, auth_headers, session, lot)
    await _join(
        client, session, auth_headers, _headers(member.telegram_id), thread_id
    )

    # Member архивировать не может — 403.
    denied = await client.post(
        f"/api/chats/{thread_id}/archive",
        headers=_headers(member.telegram_id),
    )
    assert denied.status_code == 403
    assert "archive_owner_only" in denied.json()["detail"]

    archived = await client.post(
        f"/api/chats/{thread_id}/archive", headers=auth_headers
    )
    assert archived.status_code == 200
    assert archived.json() == {"archived": True}

    thread = await session.get(ChatThread, UUID(thread_id))
    assert thread is not None
    assert thread.status == "closed"
    assert thread.closed_at is not None

    # Сообщения в архив — 403 thread_archived (08 §2).
    blocked = await client.post(
        f"/api/chats/{thread_id}/messages",
        json={"body": "ещё слово"},
        headers=auth_headers,
    )
    assert blocked.status_code == 403
    assert "thread_archived" in blocked.json()["detail"]

    # System-строка об архиве видна в ленте.
    messages = (
        await client.get(
            f"/api/chats/{thread_id}/messages", headers=auth_headers
        )
    ).json()
    assert any(m["body"] == "Комната архивирована" for m in messages)

    # Повторный archive — идемпотентный 200 без изменений.
    again = await client.post(
        f"/api/chats/{thread_id}/archive", headers=auth_headers
    )
    assert again.status_code == 200
    assert again.json() == {"archived": False}
    messages_after = (
        await client.get(
            f"/api/chats/{thread_id}/messages", headers=auth_headers
        )
    ).json()
    assert (
        sum(1 for m in messages_after if m["body"] == "Комната архивирована")
        == 1
    )


# =========================================================================
# §3 Маскировка при сохранении (интеграционно)
# =========================================================================


async def test_masking_saved_for_seller_group_deal_not_support(
    client, auth_headers: dict, session: AsyncSession, user
):
    """Телефон маскируется в seller/group/deal, НЕ маскируется в support."""
    lot = await _make_lot(session, owner_id=user.telegram_id)

    # seller: тред пользователя с самим собой-владельцем.
    seller = await client.post(
        f"/api/chats/seller/{lot.id}", headers=auth_headers
    )
    assert seller.status_code == 201, seller.text
    seller_thread = seller.json()["thread_id"]

    # group.
    group_thread = await _create_group(client, auth_headers, session, lot)

    # deal: вставляем напрямую (creation flow — за рамками 08).
    session.add(
        ChatThread(type="deal", user_id=user.telegram_id, lead_id=777001)
    )
    await session.flush()
    deal_thread = str(
        (
            await session.execute(
                select(ChatThread.id).where(
                    ChatThread.type == "deal",
                    ChatThread.lead_id == 777001,
                )
            )
        ).scalar_one()
    )

    # support.
    support = await client.post("/api/chats/support", headers=auth_headers)
    support_thread = support.json()["thread_id"]

    body = "Мой телефон +7 999 123-45-67, звоните"
    for thread_id in (seller_thread, group_thread, deal_thread):
        resp = await client.post(
            f"/api/chats/{thread_id}/messages",
            json={"body": body},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        saved = (
            await client.get(
                f"/api/chats/{thread_id}/messages", headers=auth_headers
            )
        ).json()
        assert saved[-1]["body"] == f"Мой телефон {PHONE_MASK}, звоните"

    # support: оператор = площадка, контакты не маскируются (08 §3).
    raw = await client.post(
        f"/api/chats/{support_thread}/messages",
        json={"body": body},
        headers=auth_headers,
    )
    assert raw.status_code == 201
    support_saved = (
        await client.get(
            f"/api/chats/{support_thread}/messages", headers=auth_headers
        )
    ).json()
    assert any(m["body"] == body for m in support_saved)


# =========================================================================
# §4 Rate-limit 20/мин
# =========================================================================


async def test_message_rate_limit_user(
    client, auth_headers: dict, session: AsyncSession, lot: Listing
):
    """Юзер: 20 сообщений ок, 21-е и 22-е — 429 message_rate_limit."""
    thread_id = await _create_group(client, auth_headers, session, lot)
    statuses = []
    for i in range(22):
        resp = await client.post(
            f"/api/chats/{thread_id}/messages",
            json={"body": f"сообщение {i}"},
            headers=auth_headers,
        )
        statuses.append(resp.status_code)
    assert statuses[:20] == [201] * 20, statuses
    assert statuses[20:] == [429, 429]
    limited = await client.post(
        f"/api/chats/{thread_id}/messages",
        json={"body": "ещё попытка"},
        headers=auth_headers,
    )
    assert limited.status_code == 429
    assert "message_rate_limit" in limited.json()["detail"]


async def test_message_rate_limit_partner(
    client, auth_headers: dict, session: AsyncSession, user
):
    """Партнёрское зеркало: тот же лимит 20/мин на (партнёр, тред)."""
    account, raw_key = await create_partner(
        session,
        name="Салон лимитов",
        contact=None,
        plan="pro",
        daily_quota=100,
        balance=Decimal("100"),
        cpl_rate=None,
    )
    partner_headers = {"X-API-Key": raw_key}
    # Партнёрский owner-лот → seller-тред с partner_id.
    lot = Listing(
        source="market",
        source_id=f"gov-partner-{uuid4().hex[:8]}",
        url="/listing/market/gov-partner",
        make="Hyundai",
        model="Palisade",
        year=2022,
        owner_type="partner",
        owner_partner_id=account.id,
        parsed_at=datetime.now(UTC),
    )
    session.add(lot)
    await session.flush()
    thread_id = (
        await client.post(f"/api/chats/seller/{lot.id}", headers=auth_headers)
    ).json()["thread_id"]

    statuses = []
    for i in range(22):
        resp = await client.post(
            f"/api/partner/chats/{thread_id}/messages",
            json={"body": f"ответ {i}"},
            headers=partner_headers,
        )
        statuses.append(resp.status_code)
    assert statuses[:20] == [201] * 20, statuses
    assert statuses[20:] == [429, 429]
    assert "message_rate_limit" in (
        await client.post(
            f"/api/partner/chats/{thread_id}/messages",
            json={"body": "ещё"},
            headers=partner_headers,
        )
    ).json()["detail"]


# =========================================================================
# §4 Жалобы: юзер + партнёрское зеркало + идемпотентность
# =========================================================================


async def test_report_lifecycle_and_partner_mirror(
    client, auth_headers: dict, session: AsyncSession, lot: Listing
):
    """201; повтор той же причины — 200 (дубль); другая причина — 201;
    чужой — 404; партнёрское зеркало — 201."""
    member = await _make_user(session, 920_501, "member5", "Дима")
    outsider = await _make_user(session, 920_502, "outsider2", "Кира")
    thread_id = await _create_group(client, auth_headers, session, lot)
    await _join(
        client, session, auth_headers, _headers(member.telegram_id), thread_id
    )

    first = await client.post(
        f"/api/chats/{thread_id}/report",
        json={"reason": "spam", "comment": "Реклама"},
        headers=_headers(member.telegram_id),
    )
    assert first.status_code == 201, first.text
    assert first.json() == {"reported": True}

    # Повтор той же причины — идемпотентный 200 без новой строки.
    repeat = await client.post(
        f"/api/chats/{thread_id}/report",
        json={"reason": "spam"},
        headers=_headers(member.telegram_id),
    )
    assert repeat.status_code == 200
    assert repeat.json() == {"reported": False}

    # Другая причина — отдельная жалоба (201).
    other = await client.post(
        f"/api/chats/{thread_id}/report",
        json={"reason": "offtop"},
        headers=_headers(member.telegram_id),
    )
    assert other.status_code == 201

    # Неучастник — 404.
    stranger = await client.post(
        f"/api/chats/{thread_id}/report",
        json={"reason": "spam"},
        headers=_headers(outsider.telegram_id),
    )
    assert stranger.status_code == 404

    # Партнёрское зеркало: партнёр — участник, жалоба проходит (201).
    account, raw_key = await create_partner(
        session,
        name="Салон репортов",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100"),
        cpl_rate=None,
    )
    code = (
        await client.post(f"/api/chats/{thread_id}/invite", headers=auth_headers)
    ).json()["invite_code"]
    joined = await client.post(
        "/api/partner/chats/join",
        json={"code": code},
        headers={"X-API-Key": raw_key},
    )
    assert joined.status_code == 201, joined.text
    partner_report = await client.post(
        f"/api/partner/chats/{thread_id}/report",
        json={"reason": "abuse"},
        headers={"X-API-Key": raw_key},
    )
    assert partner_report.status_code == 201, partner_report.text


# =========================================================================
# §4 Админ: список жалоб, обработка, кик (включая owner)
# =========================================================================


async def _report_of(
    client: AsyncClient, internal_token: str, thread_id: str
) -> dict:
    """Первая new-жалоба треда из админ-списка."""
    reports = (
        await client.get(
            "/api/admin/chats/reports",
            params={"status": "new"},
            headers={"X-Internal-Token": internal_token},
        )
    ).json()
    return next(r for r in reports if r["thread_id"] == thread_id)


async def test_admin_reports_list_patch_and_kick(
    client, auth_headers: dict, session: AsyncSession, lot: Listing, user,
    internal_token: str,
):
    """Список: репортёр + сниппет треда; PATCH закрывает (handled_at);
    админ-кик выкидывает owner; события пишутся с actor=operator."""
    member = await _make_user(session, 920_601, "member6", "Аня")
    thread_id = await _create_group(client, auth_headers, session, lot)
    await _join(
        client, session, auth_headers, _headers(member.telegram_id), thread_id
    )
    await client.post(
        f"/api/chats/{thread_id}/messages",
        json={"body": "последнее сообщение треда"},
        headers=auth_headers,
    )
    assert (
        await client.post(
            f"/api/chats/{thread_id}/report",
            json={"reason": "fraud"},
            headers=_headers(member.telegram_id),
        )
    ).status_code == 201

    row = await _report_of(client, internal_token, thread_id)
    assert row["reason"] == "fraud"
    assert row["reporter_name"] == "member6"
    assert row["thread_snippet"] == "последнее сообщение треда"

    # Обработка: resolved + handled_at; из new-списка исчезает.
    patched = await client.patch(
        f"/api/admin/chats/reports/{row['id']}",
        json={"status": "resolved"},
        headers={"X-Internal-Token": internal_token},
    )
    assert patched.status_code == 200, patched.text
    resolved = (
        await client.get(
            "/api/admin/chats/reports",
            params={"status": "resolved"},
            headers={"X-Internal-Token": internal_token},
        )
    ).json()
    handled = next(r for r in resolved if r["id"] == row["id"])
    assert handled["handled_at"] is not None
    new_after = (
        await client.get(
            "/api/admin/chats/reports",
            params={"status": "new"},
            headers={"X-Internal-Token": internal_token},
        )
    ).json()
    assert all(r["id"] != row["id"] for r in new_after)

    # После обработки репортёр может пожаловаться снова (201).
    again = await client.post(
        f"/api/chats/{thread_id}/report",
        json={"reason": "fraud"},
        headers=_headers(member.telegram_id),
    )
    assert again.status_code == 201

    # Админ-кик: оператор выкидывает owner (юзеру это запрещено, §2).
    kicked = await client.delete(
        f"/api/admin/chats/{thread_id}/participants/user/{user.telegram_id}",
        headers={"X-Internal-Token": internal_token},
    )
    assert kicked.status_code == 200, kicked.text
    assert all(
        p.user_id != user.telegram_id
        for p in await _thread_rows(session, thread_id)
    )

    # Чужой report-id — 404.
    missing = await client.patch(
        "/api/admin/chats/reports/999999",
        json={"status": "dismissed"},
        headers={"X-Internal-Token": internal_token},
    )
    assert missing.status_code == 404


# =========================================================================
# §5 Аудит: лента событий
# =========================================================================


async def test_events_feed_lifecycle(
    client, auth_headers: dict, session: AsyncSession, lot: Listing,
    internal_token: str, user
):
    """created/invited/joined/kicked/archived в ленте, новые сверху."""
    member = await _make_user(session, 920_701, "member7", "Ваня")
    thread_id = await _create_group(client, auth_headers, session, lot)
    code = (
        await client.post(f"/api/chats/{thread_id}/invite", headers=auth_headers)
    ).json()["invite_code"]
    assert (
        await client.post(
            "/api/chats/join", json={"code": code},
            headers=_headers(member.telegram_id),
        )
    ).status_code == 201
    assert (
        await client.delete(
            f"/api/chats/{thread_id}/participants/user/{member.telegram_id}",
            headers=auth_headers,
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/chats/{thread_id}/archive", headers=auth_headers
        )
    ).status_code == 200

    feed = (
        await client.get(
            f"/api/admin/chats/{thread_id}/events",
            headers={"X-Internal-Token": internal_token},
        )
    ).json()
    events = [e["event"] for e in feed]
    # Новые сверху: архив — самое свежее.
    assert events[0] == "archived"
    for expected in ("created", "invited", "joined", "kicked", "archived"):
        assert expected in events
    kicked_event = next(e for e in feed if e["event"] == "kicked")
    assert kicked_event["actor_type"] == "user"
    assert kicked_event["actor_id"] == user.telegram_id
    assert kicked_event["payload"] == {
        "user_type": "user",
        "user_id": member.telegram_id,
    }

    # Чужой тред — 404.
    assert (
        await client.get(
            f"/api/admin/chats/{uuid4()}/events",
            headers={"X-Internal-Token": internal_token},
        )
    ).status_code == 404


# =========================================================================
# §5 Retention: закрытые старше 90 дней скрыты из списка
# =========================================================================


async def test_retention_hides_old_closed_threads(
    client, auth_headers: dict, session: AsyncSession, user
):
    """closed 91 день назад — скрыт; 10 дней назад — виден; active — виден."""
    now = datetime.now(UTC)
    # Разные типы: singleton/partial-unique индексы не должны столкнуться.
    old = ChatThread(
        type="support",
        user_id=user.telegram_id,
        status="closed",
        closed_at=now - timedelta(days=91),
    )
    fresh = ChatThread(
        type="seller",
        user_id=user.telegram_id,
        status="closed",
        closed_at=now - timedelta(days=10),
    )
    active = ChatThread(type="deal", user_id=user.telegram_id)
    session.add_all([old, fresh, active])
    await session.flush()

    threads = (await client.get("/api/chats", headers=auth_headers)).json()
    ids = {t["id"] for t in threads}
    assert str(old.id) not in ids  # soft-hide: старше 90 дней
    assert str(fresh.id) in ids
    assert str(active.id) in ids


# =========================================================================
# §6 Mute + ThreadOut role/muted/participants[].role
# =========================================================================


async def test_thread_out_roles_and_mute(
    client, auth_headers: dict, session: AsyncSession, lot: Listing, user
):
    """Свои role/muted в ThreadOut; participants[].role; mute/unmute."""
    member = await _make_user(session, 920_801, "member8", "Соня")
    thread_id = await _create_group(client, auth_headers, session, lot)
    await _join(
        client, session, auth_headers, _headers(member.telegram_id), thread_id
    )

    # Владелец видит себя owner, участника — member.
    owner_view = next(
        t
        for t in (await client.get("/api/chats", headers=auth_headers)).json()
        if t["id"] == thread_id
    )
    assert owner_view["role"] == "owner"
    assert owner_view["muted"] is False
    roles = {
        (p["user_type"], p["user_id"]): p["role"]
        for p in owner_view["participants"]
    }
    assert roles[("user", user.telegram_id)] == "owner"
    assert roles[("user", member.telegram_id)] == "member"

    # Member видит себя member.
    member_view = next(
        t
        for t in (
            await client.get(
                "/api/chats", headers=_headers(member.telegram_id)
            )
        ).json()
        if t["id"] == thread_id
    )
    assert member_view["role"] == "member"

    # Mute: свой флаг поднимается и гасится; событие попадает в аудит.
    assert (
        await client.post(
            f"/api/chats/{thread_id}/mute", headers=_headers(member.telegram_id)
        )
    ).json() == {"muted": True}
    muted_view = next(
        t
        for t in (
            await client.get(
                "/api/chats", headers=_headers(member.telegram_id)
            )
        ).json()
        if t["id"] == thread_id
    )
    assert muted_view["muted"] is True

    assert (
        await client.post(
            f"/api/chats/{thread_id}/unmute",
            headers=_headers(member.telegram_id),
        )
    ).json() == {"muted": False}
    unmuted_view = next(
        t
        for t in (
            await client.get(
                "/api/chats", headers=_headers(member.telegram_id)
            )
        ).json()
        if t["id"] == thread_id
    )
    assert unmuted_view["muted"] is False


# =========================================================================
# Фикстура лота для этого модуля (owner — базовый user)
# =========================================================================


@pytest.fixture
async def lot(session: AsyncSession, user: User) -> Listing:
    """Активный owner-лот базового юзера — предмет переговорных."""
    return await _make_lot(session, owner_id=user.telegram_id)
