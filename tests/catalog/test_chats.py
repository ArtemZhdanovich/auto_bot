"""Чаты: треды (support/seller), сообщения, unread, ws-token, proxy, превью.

Сквозные API-тести на тестовой БД (client-фикстура): правила сценариев
(reuse, длина, closed), форма centrifugo-токенов и SSRF-фильтр превью.
"""

from __future__ import annotations

import socket as socket_module
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt as pyjwt
import pytest
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.chat.ports import ChatPublisher
from app.config import get_settings
from app.infrastructure.db.models import ChatThread, Listing
from app.infrastructure.services.link_preview import HttpLinkFetcher
from tests.conftest import install_test_container

from sqlalchemy import select

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import ChatThread

pytestmark = pytest.mark.asyncio


async def _support(client: AsyncClient, headers: dict) -> dict:
    """Создать тред поддержки и вернуть тело ответа."""
    resp = await client.post("/api/chats/support", headers=headers)
    assert resp.status_code == 201
    return resp.json()


async def test_support_thread_created_then_reused(
    client: AsyncClient, auth_headers: dict, user
):
    """201 при создании singleton-треда, 200 и тот же id при повторе."""
    first = await client.post("/api/chats/support", headers=auth_headers)
    assert first.status_code == 201
    thread_id = first.json()["thread_id"]

    second = await client.post("/api/chats/support", headers=auth_headers)
    assert second.status_code == 200
    assert second.json()["thread_id"] == thread_id

    # Системное приветствие (§6) лежит в треде
    messages = await client.get(
        f"/api/chats/{thread_id}/messages", headers=auth_headers
    )
    assert messages.status_code == 200
    body = messages.json()
    assert body[0]["sender_type"] == "system"
    assert "человек подключится" in body[0]["body"]


async def test_seller_chat_reuse_and_missing(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, user
):
    """Seller-тред на owner-лоте: 201 → 200 (тот же id); нет лота — 404."""
    owner_lot = Listing(
        source="market",
        source_id="seller-reuse-1",
        url="/listing/market/seller-reuse-1",
        make="Kia",
        model="Sorento",
        year=2022,
        price=25000,
        currency="USD",
        owner_type="user",
        owner_user_id=user.telegram_id,
        parsed_at=datetime.now(UTC),
    )
    session.add(owner_lot)
    await session.flush()

    first = await client.post(
        f"/api/chats/seller/{owner_lot.id}", headers=auth_headers
    )
    assert first.status_code == 201
    thread_id = first.json()["thread_id"]

    second = await client.post(
        f"/api/chats/seller/{owner_lot.id}", headers=auth_headers
    )
    assert second.status_code == 200
    assert second.json()["thread_id"] == thread_id

    # Свой лот: partner_id нет (тред пользователей, второй строки участника
    # для самого себя не создаётся — проверяется в test_market_lots).
    thread = (
        await session.execute(
            select(ChatThread).where(ChatThread.id == UUID(thread_id))
        )
    ).scalar_one()
    assert thread.partner_id is None

    missing = await client.post(
        "/api/chats/seller/999999999", headers=auth_headers
    )
    assert missing.status_code == 404


async def test_seller_chat_external_lot_conflict(
    client: AsyncClient, auth_headers: dict, listing
):
    """Внешний лот (owner IS NULL) — 409 market_lot_required (06 §5)."""
    resp = await client.post(
        f"/api/chats/seller/{listing.id}", headers=auth_headers
    )
    assert resp.status_code == 409
    assert "market_lot_required" in resp.json()["detail"]


async def test_messages_pagination_before_and_after(
    client: AsyncClient, auth_headers: dict
):
    """before_id — пагинация вниз (по возрастанию в ответе), after_id — поллинг."""
    created = await _support(client, auth_headers)
    thread_id = created["thread_id"]

    sent_ids = []
    for i in range(5):
        resp = await client.post(
            f"/api/chats/{thread_id}/messages",
            json={"body": f"msg {i}"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        sent_ids.append(resp.json()["id"])

    # Все сообщения по возрастанию (приветствие + 5)
    all_messages = await client.get(
        f"/api/chats/{thread_id}/messages", headers=auth_headers
    )
    ids = [m["id"] for m in all_messages.json()]
    assert ids == sorted(ids) and len(ids) == 6

    # Страница вниз от 3-го отправленного: БЛИЖАЙШИЕ старые (история при
    # скролле вверх), лимит 2 → sent[0..1] в хронологическом порядке
    page = await client.get(
        f"/api/chats/{thread_id}/messages",
        params={"before_id": sent_ids[2], "limit": 2},
        headers=auth_headers,
    )
    assert [m["id"] for m in page.json()] == [sent_ids[0], sent_ids[1]]

    # Поллинг-инкремент: всё, что после 2-го сообщения
    tail = await client.get(
        f"/api/chats/{thread_id}/messages",
        params={"after_id": sent_ids[1]},
        headers=auth_headers,
    )
    assert [m["id"] for m in tail.json()] == ids[3:]


async def test_send_message_rules(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    """404 чужой тред; 403 закрытый; 422 пустое/длинное тело."""
    created = await _support(client, auth_headers)
    thread_id = created["thread_id"]

    assert (
        await client.post(
            f"/api/chats/{thread_id}/messages",
            json={"body": ""},
            headers=auth_headers,
        )
    ).status_code == 422
    assert (
        await client.post(
            f"/api/chats/{thread_id}/messages",
            json={"body": "x" * 4001},
            headers=auth_headers,
        )
    ).status_code == 422
    assert (
        await client.post(
            f"/api/chats/{UUID(int=0)}/messages",
            json={"body": "hi"},
            headers=auth_headers,
        )
    ).status_code == 404

    # Закрытый тред → read-only (§2)
    await session.execute(
        update(ChatThread)
        .where(ChatThread.id == UUID(thread_id))
        .values(status="closed")
    )
    await session.flush()
    resp = await client.post(
        f"/api/chats/{thread_id}/messages",
        json={"body": "после закрытия"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert "thread_archived" in resp.json()["detail"]


async def test_unread_and_mark_read(client: AsyncClient, auth_headers: dict):
    """Unread растёт с сообщениями и обнуляется read-маркером."""
    created = await _support(client, auth_headers)
    thread_id = created["thread_id"]

    # Приветствие (system) — непрочитано
    threads = (await client.get("/api/chats", headers=auth_headers)).json()
    mine = next(t for t in threads if t["id"] == thread_id)
    assert mine["unread"] == 1

    await client.post(
        f"/api/chats/{thread_id}/messages",
        json={"body": "вопрос"},
        headers=auth_headers,
    )
    # Собственное сообщение не считается непрочитанным
    threads = (await client.get("/api/chats", headers=auth_headers)).json()
    mine = next(t for t in threads if t["id"] == thread_id)
    assert mine["unread"] == 1

    read = await client.post(
        f"/api/chats/{thread_id}/read", json={}, headers=auth_headers
    )
    assert read.status_code == 200
    threads = (await client.get("/api/chats", headers=auth_headers)).json()
    mine = next(t for t in threads if t["id"] == thread_id)
    assert mine["unread"] == 0


async def test_ws_token_form(client: AsyncClient, auth_headers: dict, user):
    """GET /api/chats/ws-token: sub=user:{id}, exp ≈ +12ч, подпись секретом."""
    settings = get_settings()
    resp = await client.get("/api/chats/ws-token", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    expected_url = settings.centrifugo_public_url or settings.centrifugo_url
    assert body["url"] == expected_url

    claims = pyjwt.decode(
        body["token"],
        settings.centrifugo_secret.get_secret_value(),
        algorithms=["HS256"],
    )
    assert claims["sub"] == f"user:{user.telegram_id}"
    exp = datetime.fromtimestamp(claims["exp"], tz=UTC)
    remaining = exp - datetime.now(UTC)
    assert timedelta(hours=11) < remaining <= timedelta(hours=12, minutes=1)


async def _proxy_call(
    client: AsyncClient, token: str, channel: str, sub: str | None
):
    """POST /api/chats/subscribe-proxy с телом centrifugo v6."""
    return await client.post(
        "/api/chats/subscribe-proxy",
        json={"client": "c-1", "channel": channel, "user": sub},
        headers={"X-Internal-Token": token},
    )


async def test_subscribe_proxy_personal_and_thread(
    client: AsyncClient, auth_headers: dict, internal_token: str, user
):
    """Личный канал — только свой; thread — по участию; без токена — 401."""
    assert (
        await client.post(
            "/api/chats/subscribe-proxy",
            json={"client": "c-1", "channel": f"user:{user.telegram_id}"},
        )
    ).status_code == 401

    own = await _proxy_call(
        client, internal_token, f"user:{user.telegram_id}", f"user:{user.telegram_id}"
    )
    assert own.status_code == 200
    assert own.json() == {}

    foreign = await _proxy_call(
        client, internal_token, "user:123456789", f"user:{user.telegram_id}"
    )
    assert foreign.json()["error"]["code"] == 403

    created = await _support(client, auth_headers)
    thread_id = created["thread_id"]
    ok = await _proxy_call(
        client, internal_token, f"thread:{thread_id}", f"user:{user.telegram_id}"
    )
    assert ok.json() == {}

    outsider = await _proxy_call(
        client, internal_token, f"thread:{thread_id}", "user:888888888"
    )
    assert outsider.json()["error"]["code"] == 403
    garbage = await _proxy_call(client, internal_token, "thread:not-a-uuid", "user:1")
    assert garbage.json()["error"]["code"] == 403


async def test_link_preview_internal_listing(
    client: AsyncClient, auth_headers: dict, listing
):
    """/listing/N → карточка из БД (модель/цена/ссылка)."""
    created = await _support(client, auth_headers)
    resp = await client.get(
        f"/api/chats/{created['thread_id']}/link-preview",
        params={"url": f"/listing/{listing.id}"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    preview = resp.json()
    assert preview["kind"] == "listing"
    assert preview["make"] == "Kia"
    assert preview["model"] == "Carnival"
    assert preview["price"] == 30000.0
    assert preview["url"] == listing.url


async def test_link_preview_ssrf_private_ip_rejected(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Приватный IP хоста → LinkBlockedError → 400 (§10: SSRF критичен)."""
    created = await _support(client, auth_headers)

    def fake_getaddrinfo(*args, **kwargs):
        # 192.168.0.1 — приватный диапазон; формат кортежа как у getaddrinfo
        return [
            (
                socket_module.AF_INET,
                socket_module.SOCK_STREAM,
                6,
                "",
                ("192.168.0.1", 80),
            )
        ]

    monkeypatch.setattr(socket_module, "getaddrinfo", fake_getaddrinfo)
    resp = await client.get(
        f"/api/chats/{created['thread_id']}/link-preview",
        params={"url": "http://intranet.local/page"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "private_address_forbidden" in resp.json()["detail"]


async def test_link_preview_external_og_and_cache(
    client: AsyncClient, auth_headers: dict, fake_redis, monkeypatch
):
    """OG-теги парсятся, результат кэшируется на 24ч (второй заход из кэша)."""
    created = await _support(client, auth_headers)
    url = "https://example.org/news/test-drive"
    html_page = (
        "<html><head>"
        '<meta property="og:title" content="Тест-драйв Kia Carnival">'
        '<meta property="og:image" content="https://img.example.org/kia.jpg">'
        "</head></html>"
    )
    calls = {"n": 0}

    async def fake_fetch(self, url_requested: str):
        calls["n"] += 1
        return html_page

    monkeypatch.setattr(HttpLinkFetcher, "fetch", fake_fetch)
    first = await client.get(
        f"/api/chats/{created['thread_id']}/link-preview",
        params={"url": url},
        headers=auth_headers,
    )
    assert first.status_code == 200
    preview = first.json()
    assert preview["kind"] == "external"
    assert preview["title"] == "Тест-драйв Kia Carnival"
    assert preview["image"] == "https://img.example.org/kia.jpg"
    assert preview["domain"] == "example.org"

    # Второй запрос идёт в кэш (fetcher не вызывается), TTL 24 часа
    second = await client.get(
        f"/api/chats/{created['thread_id']}/link-preview",
        params={"url": url},
        headers=auth_headers,
    )
    assert second.json() == preview
    assert calls["n"] == 1
    assert any(k.startswith("chat:preview:") for k in fake_redis.data)
    assert any(ttl == 24 * 60 * 60 for ttl in fake_redis.ttls.values())


# =========================================================================
# Юнит-тесты без БД (pure-логика: proxy-валидация, OG-парсер, JWT)
# =========================================================================


class _FakeChatRepo:
    """Стаб ChatRepository: is_participant по флагу."""

    def __init__(self, participant: bool) -> None:
        self.participant = participant

    async def is_participant(self, thread_id, user_type: str, user_id: int) -> bool:
        return self.participant


async def test_subscribe_proxy_rules_unit():
    """Правила валидации каналов: личный — строго свой, thread — участнику."""
    from app.application.chat.use_cases import HandleSubscribeProxy

    allow = HandleSubscribeProxy(_FakeChatRepo(True))
    deny = HandleSubscribeProxy(_FakeChatRepo(False))

    # Личный канал
    assert await allow.execute("user:42", "user:42") is True
    assert await allow.execute("user:42", "user:43") is False

    # Тред: участие решает
    assert (
        await allow.execute("thread:00000000-0000-0000-0000-000000000001", "user:42")
        is True
    )
    assert (
        await deny.execute("thread:00000000-0000-0000-0000-000000000001", "user:42")
        is False
    )
    # Партнёрский sub
    assert (
        await allow.execute("thread:00000000-0000-0000-0000-000000000001", "partner:7")
        is True
    )
    # Мусор: не-uuid, чужой префикс, битый sub
    assert await allow.execute("thread:not-a-uuid", "user:42") is False
    assert await allow.execute("news:42", "user:42") is False
    assert await allow.execute("thread:00000000-0000-0000-0000-000000000001", "42") is (
        False
    )
    assert await allow.execute("thread:00000000-0000-0000-0000-000000000001", None) is (
        False
    )


async def test_parse_og_tags_unit():
    """OG-парсер: прямой/обратный порядок атрибутов, HTML-сущности, fallback."""
    from app.application.chat.link_preview import parse_og

    html_direct = (
        '<meta property="og:title" content="Kia &amp; Co">'
        '<meta property="og:image" content="https://img/x.jpg">'
    )
    preview = parse_og(html_direct, "https://example.com/a")
    assert preview["kind"] == "external"
    assert preview["title"] == "Kia & Co"
    assert preview["image"] == "https://img/x.jpg"
    assert preview["domain"] == "example.com"

    html_reversed = '<meta content="Описание" property="og:description">'
    assert parse_og(html_reversed, "https://example.com/a")["description"] == (
        "Описание"
    )

    empty = parse_og("<html></html>", "https://example.com/a")
    assert empty["title"] == "example.com" and empty["image"] is None


def test_centrifugo_jwt_form_unit():
    """centrifugo_jwt: HS256, sub/iat/exp (exp = now + ttl)."""
    from app.infrastructure.services.centrifugo import centrifugo_jwt

    token = centrifugo_jwt("user:42", "unit-secret", ttl_sec=60)
    claims = pyjwt.decode(token, "unit-secret", algorithms=["HS256"])
    assert claims["sub"] == "user:42"
    assert claims["exp"] - claims["iat"] == 60

    with pytest.raises(pyjwt.InvalidSignatureError):
        pyjwt.decode(token, "wrong-secret", algorithms=["HS256"])


# =========================================================================
# Идемпотентность POST /messages (долг ревизора №7)
# =========================================================================


class _PublishCounter:
    """Мок ChatPublisher: запоминает каждый вызов publish (канал, payload)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def publish(self, channel: str, data: dict) -> None:
        self.calls.append((channel, dict(data)))


async def test_send_message_idempotency_key_replay(
    session: AsyncSession, fake_redis, auth_headers: dict, user
):
    """Двойной POST с одним Idempotency-Key: тот же message_id, 200 вместо
    201 и ровно одна публикация в centrifugo (повтор не публикуется)."""
    from app.main_api import app as fastapi_app

    counter = _PublishCounter()

    class _PublisherOverride(Provider):
        """Подмена ChatPublisher счётчиком (publisher — APP-скоуп)."""

        @provide(scope=Scope.APP)
        def chat_publisher(self) -> ChatPublisher:
            return counter

    prev_container = fastapi_app.state.dishka_container
    container = install_test_container(
        fastapi_app,
        session=session,
        redis=fake_redis,
        extra=(_PublisherOverride(),),
    )
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            created = await http.post("/api/chats/support", headers=auth_headers)
            thread_id = created.json()["thread_id"]
            headers = {**auth_headers, "Idempotency-Key": "idem-1"}

            first = await http.post(
                f"/api/chats/{thread_id}/messages",
                json={"body": "привет"},
                headers=headers,
            )
            assert first.status_code == 201
            message_id = first.json()["id"]

            # Повтор того же запроса (сетевой ретрай): 200 + то же сообщение
            second = await http.post(
                f"/api/chats/{thread_id}/messages",
                json={"body": "привет"},
                headers=headers,
            )
            assert second.status_code == 200
            assert second.json()["id"] == message_id

            # В треде одна запись от этого ключа; publish был один
            # (09 §1: личный канал владельца, каналов thread:* больше нет)
            assert not [c for c in counter.calls if c[0].startswith("thread:")]
            user_calls = [
                c
                for c in counter.calls
                if c[0] == f"user:{user.telegram_id}"
                and c[1]["type"] == "message"
            ]
            assert len(user_calls) == 1
            messages = await http.get(
                f"/api/chats/{thread_id}/messages", headers=auth_headers
            )
            user_bodies = [
                m
                for m in messages.json()
                if m["sender_type"] == "user" and m["body"] == "привет"
            ]
            assert len(user_bodies) == 1

            # message_id сохранён под ключ chat:idem:… с TTL 24ч
            idem_keys = [k for k in fake_redis.data if k.startswith("chat:idem:")]
            assert len(idem_keys) == 1
            assert fake_redis.data[idem_keys[0]] == str(message_id)
            assert fake_redis.ttls[idem_keys[0]] == 24 * 60 * 60

            # Другой ключ — новое сообщение (201), без дедупликации
            third = await http.post(
                f"/api/chats/{thread_id}/messages",
                json={"body": "другое"},
                headers={**auth_headers, "Idempotency-Key": "idem-2"},
            )
            assert third.status_code == 201
            assert third.json()["id"] != message_id
    finally:
        fastapi_app.state.dishka_container = prev_container
        await container.close()
