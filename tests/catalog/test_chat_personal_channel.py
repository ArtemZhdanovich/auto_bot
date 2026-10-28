"""Личный канал + единый стор чатов (09 §1, волна B): транспорт бэка.

Юнит-тесты обёртки `ParticipantEventPublisher` (личные каналы участников,
уникальность, без thread:*) и сквозные API-тесты: полный MessageView в
payload, typing другим участникам (+партнёрское зеркало, rate 1/2с),
online-статусы через presence centrifugo, публикация говернанса в
личные каналы.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.chat.dto import (
    MessageView,
    ParticipantRef,
    ParticipantView,
    SendMessageCommand,
    ThreadRecord,
)
from app.application.chat.ports import ChannelPresencePort, ChatPublisher
from app.application.chat.publisher import ParticipantEventPublisher
from app.application.chat.use_cases import SendMessage, message_event
from app.config import get_settings
from app.infrastructure.db.models import Listing, User
from app.infrastructure.security.tokens import create_access_token
from tests.partners.helpers import create_partner

pytestmark = pytest.mark.asyncio

OWNER_ID = 900_501
PARTNER_ID = 77
THREAD_ID = UUID("00000000-0000-0000-0000-0000000009c1")


class FakeThreadRepo:
    """Репозиторий-двойник: тред + строки участников (для обёртки)."""

    def __init__(
        self,
        thread: ThreadRecord,
        participants: list[ParticipantView] | None = None,
    ) -> None:
        self._thread = thread
        self.participants = participants or []

    async def get_thread(self, thread_id: UUID) -> ThreadRecord | None:
        return self._thread if thread_id == self._thread.id else None

    async def list_participants(
        self, thread_id: UUID
    ) -> list[ParticipantView]:
        return list(self.participants)


class FakeSendRepo(FakeThreadRepo):
    """Репозиторий для SendMessage: доступ владельца + запись сообщения."""

    async def get_participant_thread(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> ThreadRecord | None:
        if user_type != "user" or user_id != self._thread.user_id:
            return None
        return self._thread

    async def add_message(self, command: SendMessageCommand) -> MessageView:
        return MessageView(
            id=17,
            sender_type=command.sender.user_type,
            sender_id=command.sender.user_id,
            body=command.body,
            created_at=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
        )


class FakeUoW:
    """UnitOfWork-заглушка: считает коммиты."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakePublisher:
    """Мок centrifugo: запоминает (канал, payload) каждого publish."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, channel: str, data: dict[str, Any]) -> None:
        self.calls.append((channel, dict(data)))

    def channels(self) -> list[str]:
        return [ch for ch, _ in self.calls]

    def events_of(self, channel: str) -> list[dict[str, Any]]:
        return [data for ch, data in self.calls if ch == channel]


def _thread(
    *,
    user_id: int = OWNER_ID,
    partner_id: int | None = PARTNER_ID,
    type: str = "seller",  # noqa: A002 — поле ThreadRecord
) -> ThreadRecord:
    return ThreadRecord(
        id=THREAD_ID, type=type, user_id=user_id, partner_id=partner_id
    )


async def test_publisher_fans_out_to_unique_personal_channels():
    """Событие уходит владельцу, партнёру счёта и участникам; без дублей
    и без каналов thread:* (09 §1)."""
    repo = FakeThreadRepo(
        _thread(),
        participants=[
            ParticipantView(user_type="user", user_id=OWNER_ID, role="owner"),
            ParticipantView(user_type="user", user_id=42),
            ParticipantView(user_type="partner", user_id=PARTNER_ID),
            ParticipantView(user_type="partner", user_id=5),
            # операторам личных каналов нет — строка пропускается
            ParticipantView(user_type="operator", user_id=1),
        ],
    )
    publisher = FakePublisher()
    events = ParticipantEventPublisher(repo, publisher)  # type: ignore[arg-type]

    await events.publish_to_participants(
        THREAD_ID, {"type": "message", "body": "привет"}
    )

    assert publisher.channels() == [
        f"user:{OWNER_ID}",
        f"partner:{PARTNER_ID}",
        "user:42",
        "partner:5",
    ]
    assert all(not ch.startswith("thread:") for ch in publisher.channels())
    for channel in publisher.channels():
        (payload,) = publisher.events_of(channel)
        assert payload["type"] == "message"
        assert payload["thread_id"] == str(THREAD_ID)
    # копия каждому: мутация одного payload не задевает соседние
    publisher.calls[0][1]["body"] = "изменено"
    assert publisher.calls[1][1]["body"] == "привет"


async def test_publisher_keeps_existing_thread_id_and_excludes_sender():
    """Свой thread_id не перезаписывается; exclude убирает канал субъекта."""
    repo = FakeThreadRepo(
        _thread(partner_id=None, type="group"),
        participants=[ParticipantView(user_type="user", user_id=42)],
    )
    publisher = FakePublisher()
    events = ParticipantEventPublisher(repo, publisher)  # type: ignore[arg-type]

    await events.publish_to_participants(
        THREAD_ID,
        {"type": "typing", "thread_id": "custom", "user_type": "user"},
        exclude=ParticipantRef(user_type="user", user_id=OWNER_ID),
    )

    assert publisher.channels() == ["user:42"]
    assert publisher.events_of("user:42")[0]["thread_id"] == "custom"


async def test_publisher_skips_missing_thread():
    """Треда нет — публикация тихо пропускается (fire-and-forget)."""
    repo = FakeThreadRepo(_thread())
    publisher = FakePublisher()
    events = ParticipantEventPublisher(repo, publisher)  # type: ignore[arg-type]

    await events.publish_to_participants(
        UUID("00000000-0000-0000-0000-0000000000ff"), {"type": "read"}
    )

    assert publisher.calls == []


async def test_send_message_payload_is_full_message_view():
    """message-событие несёт все поля MessageView — клиент не докачивает."""
    repo = FakeSendRepo(_thread(partner_id=PARTNER_ID))
    publisher = FakePublisher()
    use_case = SendMessage(
        repo,  # type: ignore[arg-type]
        ParticipantEventPublisher(repo, publisher),  # type: ignore[arg-type]
        FakeUoW(),  # type: ignore[arg-type]
    )

    message = await use_case.execute(
        THREAD_ID, ParticipantRef(user_type="user", user_id=OWNER_ID), "вопрос"
    )

    assert message.id == 17
    assert publisher.channels() == [f"user:{OWNER_ID}", f"partner:{PARTNER_ID}"]
    payload = publisher.events_of(f"user:{OWNER_ID}")[0]
    assert payload == {
        "type": "message",
        "thread_id": str(THREAD_ID),
        "id": message.id,
        "sender_type": "user",
        "sender_id": OWNER_ID,
        "body": "вопрос",
        "created_at": message.created_at.isoformat(),
    }
    assert payload == message_event(THREAD_ID, message)


# =========================================================================
# API-хелперы: контейнер с моком centrifugo + участники group-треда
# =========================================================================


class _CountingPublisher:
    """ChatPublisher-мок для API-тестов: пишет (канал, payload)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, channel: str, data: dict[str, Any]) -> None:
        self.calls.append((channel, dict(data)))

    def channels(self) -> list[str]:
        return [ch for ch, _ in self.calls]

    def events_of(self, channel: str) -> list[dict[str, Any]]:
        return [data for ch, data in self.calls if ch == channel]


class _FakePresence:
    """ChannelPresencePort-мок: карта онлайн-каналов + флаг enabled."""

    def __init__(
        self, online: dict[str, set[str]] | None = None, *, enabled: bool = True
    ) -> None:
        self.online = online or {}
        self.enabled = enabled
        self.asked: list[str] = []

    async def presence(self, channel: str) -> set[str]:
        self.asked.append(channel)
        return set(self.online.get(channel, ()))


def _publisher_override(counter: _CountingPublisher) -> Provider:
    class _P(Provider):
        @provide(scope=Scope.APP)
        def chat_publisher(self) -> ChatPublisher:
            return counter

    return _P()


def _presence_override(fake: _FakePresence) -> Provider:
    class _P(Provider):
        @provide(scope=Scope.APP)
        def channel_presence(self) -> ChannelPresencePort:
            return fake

    return _P()


async def _make_user(
    session: AsyncSession, telegram_id: int, username: str
) -> User:
    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=username.title(),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


def _headers(telegram_id: int) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(telegram_id, get_settings())}"
    }


async def _make_lot(session: AsyncSession, owner_id: int) -> Listing:
    lot = Listing(
        source="market",
        source_id=f"pc-{uuid4().hex[:12]}",
        url=f"/listing/market/pc-{uuid4().hex[:12]}",
        make="Kia",
        model="Carnival",
        year=2021,
        owner_type="user",
        owner_user_id=owner_id,
        parsed_at=datetime.now(UTC),
    )
    session.add(lot)
    await session.flush()
    return lot


async def _make_group(
    client: AsyncClient,
    owner_headers: dict[str, str],
    member_headers: dict[str, str],
    lot: Listing,
) -> str:
    """Group-тред с owner + member (invite-код, 09 §1: 2 участника)."""
    thread_id = (
        await client.post(
            "/api/chats/group",
            json={"title": "Переговоры", "listing_id": lot.id},
            headers=owner_headers,
        )
    ).json()["thread_id"]
    code = (
        await client.post(
            f"/api/chats/{thread_id}/invite", headers=owner_headers
        )
    ).json()["invite_code"]
    joined = await client.post(
        "/api/chats/join", json={"code": code}, headers=member_headers
    )
    assert joined.status_code == 201, joined.text
    return thread_id


from contextlib import asynccontextmanager
from uuid import uuid4

from tests.conftest import install_test_container


@asynccontextmanager
async def _app_client(session, fake_redis, *extra: Provider):
    """ASGI-клиент на контейнере с подменами centrifugo (как test_chats)."""
    from app.main_api import app as fastapi_app

    prev = fastapi_app.state.dishka_container
    container = install_test_container(
        fastapi_app, session=session, redis=fake_redis, extra=tuple(extra)
    )
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http
    finally:
        fastapi_app.state.dishka_container = prev
        await container.close()


# =========================================================================
# Typing: другим участникам, себе нет, rate 1/2с
# =========================================================================


async def test_typing_delivered_to_other_participants_only(
    session, fake_redis, user
):
    """Owner печатает в group-треде: typing уходит member'у, owner — нет."""
    member = await _make_user(session, 930_101, "member_typing")
    lot = await _make_lot(session, user.telegram_id)
    owner_headers = _headers(user.telegram_id)
    counter = _CountingPublisher()

    async with _app_client(
        session, fake_redis, _publisher_override(counter)
    ) as client:
        thread_id = await _make_group(
            client, owner_headers, _headers(member.telegram_id), lot
        )
        counter.calls.clear()

        resp = await client.post(
            f"/api/chats/{thread_id}/typing", headers=owner_headers
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"typing": True}
        # Себе echo нет: только личный канал второго участника
        assert counter.channels() == [f"user:{member.telegram_id}"]
        (payload,) = counter.events_of(f"user:{member.telegram_id}")
        assert payload == {
            "type": "typing",
            "thread_id": thread_id,
            "user_type": "user",
        }


async def test_typing_rate_limited_and_outsider_404(
    session, fake_redis, user
):
    """Второе typing в окне 2с — 429 typing_rate_limit; чужой тред — 404."""
    member = await _make_user(session, 930_201, "member_rl")
    outsider = await _make_user(session, 930_202, "outsider_rl")
    lot = await _make_lot(session, user.telegram_id)
    owner_headers = _headers(user.telegram_id)
    counter = _CountingPublisher()

    async with _app_client(
        session, fake_redis, _publisher_override(counter)
    ) as client:
        thread_id = await _make_group(
            client, owner_headers, _headers(member.telegram_id), lot
        )
        counter.calls.clear()

        first = await client.post(
            f"/api/chats/{thread_id}/typing", headers=owner_headers
        )
        second = await client.post(
            f"/api/chats/{thread_id}/typing", headers=owner_headers
        )
        assert first.status_code == 200
        assert second.status_code == 429
        assert "typing_rate_limit" in second.json()["detail"]

        # Чужой тред — 404 (rate-ключ чужой, лимит не мешает)
        outsider_resp = await client.post(
            f"/api/chats/{thread_id}/typing",
            headers=_headers(outsider.telegram_id),
        )
        assert outsider_resp.status_code == 404


async def test_partner_typing_mirror(session, fake_redis, auth_headers, user):
    """Партнёр печатает в seller-треде: user_type=partner юзеру, не себе."""
    account, raw_key = await create_partner(
        session,
        name="Салон typing",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100.00"),
        cpl_rate=None,
    )
    account.placement_slots = 5
    await session.flush()
    counter = _CountingPublisher()

    async with _app_client(
        session, fake_redis, _publisher_override(counter)
    ) as client:
        lot_resp = await client.post(
            "/api/partner/market/lots",
            json={
                "make": "Genesis",
                "model": "GV80",
                "year": 2023,
                "price": 65000,
                "currency": "USD",
            },
            headers={"X-API-Key": raw_key},
        )
        assert lot_resp.status_code == 201, lot_resp.text
        thread_id = (
            await client.post(
                f"/api/chats/seller/{lot_resp.json()['listing_id']}",
                headers=auth_headers,
            )
        ).json()["thread_id"]
        counter.calls.clear()

        resp = await client.post(
            f"/api/partner/chats/{thread_id}/typing",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200, resp.text

        # Только личный канал юзера-владельца; партнёру себе — нет
        assert counter.channels() == [f"user:{user.telegram_id}"]
        (payload,) = counter.events_of(f"user:{user.telegram_id}")
        assert payload == {
            "type": "typing",
            "thread_id": thread_id,
            "user_type": "partner",
        }

        # Rate-зеркало: второе событие в окне 2с — 429
        second = await client.post(
            f"/api/partner/chats/{thread_id}/typing",
            headers={"X-API-Key": raw_key},
        )
        assert second.status_code == 429
        assert "typing_rate_limit" in second.json()["detail"]


# =========================================================================
# Online: presence личных каналов centrifugo
# =========================================================================


async def test_online_via_presence(session, fake_redis, auth_headers):
    """2 из 3 онлайн (presence каналов user:{id}); disabled — пусто."""
    online_map = {"user:900001": {"c-1"}, "user:900003": {"c-2", "c-3"}}
    presence = _FakePresence(online_map)
    disabled = _FakePresence(enabled=False)

    async with _app_client(
        session, fake_redis, _presence_override(presence)
    ) as client:
        resp = await client.get(
            "/api/chats/online", params={"ids": "900001,900002,900003"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"online": [900001, 900003]}
        # Спрошены все три личных канала
        assert sorted(presence.asked) == [
            "user:900001",
            "user:900002",
            "user:900003",
        ]

    async with _app_client(
        session, fake_redis, _presence_override(disabled)
    ) as client:
        resp = await client.get(
            "/api/chats/online", params={"ids": "900001"}, headers=auth_headers
        )
        assert resp.json() == {"online": []}
        assert disabled.asked == []


async def test_online_ids_validation(session, fake_redis, auth_headers):
    """Мусор и >50 id — 400 online_ids_invalid."""
    presence = _FakePresence()
    async with _app_client(
        session, fake_redis, _presence_override(presence)
    ) as client:
        garbage = await client.get(
            "/api/chats/online", params={"ids": "1,abc,3"}, headers=auth_headers
        )
        assert garbage.status_code == 400

        too_many = ",".join(str(i) for i in range(1, 52))
        overflow = await client.get(
            "/api/chats/online", params={"ids": too_many}, headers=auth_headers
        )
        assert overflow.status_code == 400
        assert "online_ids_invalid" in overflow.json()["detail"]


async def test_partner_online_mirror(session, fake_redis):
    """Кабинет спрашивает те же юзер-каналы user:{tg_id} (09 §1)."""
    _, raw_key = await create_partner(
        session,
        name="Салон онлайн",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100.00"),
        cpl_rate=None,
    )
    presence = _FakePresence({"user:900001": {"c-1"}})

    async with _app_client(
        session, fake_redis, _presence_override(presence)
    ) as client:
        resp = await client.get(
            "/api/partner/chats/online",
            params={"ids": "900001,900002"},
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"online": [900001]}
        assert presence.asked == ["user:900001", "user:900002"]


# =========================================================================
# Говернанс и бот: события треда идут в личные каналы участников
# =========================================================================


async def test_archive_publishes_to_participants(session, fake_redis, user):
    """Архив переговорной: system-строка (полное сообщение) и meta-событие
    приходят в личные каналы обоих участников, thread:* каналов нет."""
    member = await _make_user(session, 930_301, "member_archive")
    lot = await _make_lot(session, user.telegram_id)
    owner_headers = _headers(user.telegram_id)
    counter = _CountingPublisher()

    async with _app_client(
        session, fake_redis, _publisher_override(counter)
    ) as client:
        thread_id = await _make_group(
            client, owner_headers, _headers(member.telegram_id), lot
        )
        counter.calls.clear()

        resp = await client.post(
            f"/api/chats/{thread_id}/archive", headers=owner_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"archived": True}

        assert all(not ch.startswith("thread:") for ch in counter.channels())
        for channel in (f"user:{user.telegram_id}", f"user:{member.telegram_id}"):
            payloads = counter.events_of(channel)
            types = [p["type"] for p in payloads]
            assert types == ["message", "system"]
            message = payloads[0]
            # Полный MessageView: id/sender/created_at на месте
            assert message["thread_id"] == thread_id
            assert message["sender_type"] == "system"
            assert message["body"] == "Комната архивирована"
            assert message["id"] > 0 and message["created_at"]
            assert payloads[1]["event"] == "archived"


async def test_mark_read_publishes_to_participants(session, fake_redis, user):
    """Read-маркер тоже уходит в личные каналы (единственный участник
    support-треда — сам владелец; thread:* каналов нет)."""
    counter = _CountingPublisher()
    async with _app_client(
        session, fake_redis, _publisher_override(counter)
    ) as client:
        headers = _headers(user.telegram_id)
        thread_id = (
            await client.post("/api/chats/support", headers=headers)
        ).json()["thread_id"]
        counter.calls.clear()

        resp = await client.post(
            f"/api/chats/{thread_id}/read", json={}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert counter.channels() == [f"user:{user.telegram_id}"]
        (payload,) = counter.events_of(f"user:{user.telegram_id}")
        assert payload["type"] == "read"
        assert payload["user_type"] == "user"
        assert payload["thread_id"] == thread_id


# =========================================================================
# ClaimLead: system-строка подключения агента через участников (юнит)
# =========================================================================


class _DealRepo(FakeThreadRepo):
    """Минимальный chats-репозиторий ClaimLead: deal-тред + сообщение."""

    def __init__(self, thread: ThreadRecord) -> None:
        super().__init__(thread)
        self.attached: int | None = None

    async def attach_partner_to_deal(
        self, lead_id: int, partner_id: int
    ) -> ThreadRecord:
        self.attached = partner_id
        return self._thread

    async def add_message(self, command: SendMessageCommand) -> MessageView:
        return MessageView(
            id=5,
            sender_type=command.sender.user_type,
            sender_id=None,
            body=command.body,
            created_at=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
        )


class _ClaimUoW:
    """UoW ClaimLead: async with + commit."""

    def __init__(self) -> None:
        self.commits = 0

    async def __aenter__(self) -> "_ClaimUoW":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


async def test_claim_lead_announces_agent_via_participants():
    """Успешный claim: system-строка «Агент подключился» (полный MessageView)
    уходит в личные каналы юзера и партнёра счёта, thread:* нет."""
    from tests.partners.test_partners import FakePartnerRepository

    from app.domain.partners.ports import CLAIM_OK

    from app.application.partners.use_cases import ClaimLead

    repo = FakePartnerRepository(balance=Decimal("100.00"), plan="pro")
    repo.free = {1}
    thread = _thread(partner_id=1, type="deal")
    chats = _DealRepo(thread)
    publisher = FakePublisher()

    result = await ClaimLead(
        _ClaimUoW(),  # type: ignore[arg-type]
        repo,  # type: ignore[arg-type]
        chats=chats,  # type: ignore[arg-type]
        publisher=ParticipantEventPublisher(  # type: ignore[arg-type]
            chats, publisher
        ),
    ).execute(1, 1)

    assert result.reason == CLAIM_OK
    assert publisher.channels() == [f"user:{OWNER_ID}", "partner:1"]
    payload = publisher.events_of(f"user:{OWNER_ID}")[0]
    assert payload["type"] == "message"
    assert payload["sender_type"] == "system"
    assert "подключился" in payload["body"]
    assert payload["id"] == 5 and payload["created_at"]
