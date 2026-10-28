"""Внутренний рынок: витрина (user) + офферы и чаты партнёра (X-API-Key)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.chat.ports import ChatPublisher
from app.infrastructure.db.models import ChatThread, Lead
from tests.conftest import install_test_container
from tests.partners.helpers import create_partner

pytestmark = pytest.mark.asyncio


async def _make_partner(session: AsyncSession) -> tuple[int, str]:
    """Партнёр pro + первый ключ: (partner_id, raw_key)."""
    account, raw = await create_partner(
        session,
        name="Рынок",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100.00"),
        cpl_rate=None,
    )
    return account.id, raw


async def _partner_owner_lot(
    session: AsyncSession, client: AsyncClient, account, raw_key: str
) -> int:
    """Лот партнёра через API (06 §2): слоты счёту выданы напрямую.

    Возвращает listing_id созданного owner-лота (seller-треды партнёра
    живут на owner-лотах, не на витрине внешних лотов).
    """
    account.placement_slots = 5
    await session.flush()
    resp = await client.post(
        "/api/partner/market/lots",
        json={
            "make": "Genesis",
            "model": "GV80",
            "year": 2023,
            "price": 65000,
            "currency": "USD",
            "mileage_km": 15000,
            "description": "Салон, гарантия",
            "country": "RU",  # GEO-V1 §2: страна обязательна для owner-лота
        },
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["listing_id"]


async def test_market_offers_flow(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, listing
):
    """Витрина пуста → партнёр добавляет лот → юзер видит его с note."""
    partner_id, raw = await _make_partner(session)
    assert (
        await client.get("/api/market/offers", headers=auth_headers)
    ).json() == []

    created = await client.post(
        "/api/partner/offers",
        json={"listing_id": listing.id, "note": "Срочно, торг"},
        headers={"X-API-Key": raw},
    )
    assert created.status_code == 201
    assert created.json() == {"listing_id": listing.id}

    offers = (
        await client.get("/api/market/offers", headers=auth_headers)
    ).json()
    assert len(offers) == 1
    offer = offers[0]
    assert offer["listing_id"] == listing.id
    assert offer["partner_id"] == partner_id
    assert offer["note"] == "Срочно, торг"
    assert offer["has_market"] is True
    assert offer["make"] == "Kia" and offer["price"] == 30000.0

    # Фильтры: марка мимо — пусто, по цене выше — на месте
    assert (
        await client.get(
            "/api/market/offers",
            params={"make": "BMW"},
            headers=auth_headers,
        )
    ).json() == []
    assert (
        await client.get(
            "/api/market/offers",
            params={"price_max": 31000},
            headers=auth_headers,
        )
    ).json() != []
    assert (
        await client.get(
            "/api/market/offers",
            params={"price_max": 1000},
            headers=auth_headers,
        )
    ).json() == []


async def test_offer_reuse_conflict_and_delete(
    client: AsyncClient, session: AsyncSession, listing
):
    """Повтор POST своего лота — 200 + новая note; чужой — 409; DELETE — 404."""
    _, raw = await _make_partner(session)
    _, raw_other = await _make_partner(session)

    reuse = await client.post(
        "/api/partner/offers",
        json={"listing_id": listing.id, "note": "Обновлено"},
        headers={"X-API-Key": raw},
    )
    assert reuse.status_code == 201

    updated = await client.post(
        "/api/partner/offers",
        json={"listing_id": listing.id, "note": "Ещё дешевле"},
        headers={"X-API-Key": raw},
    )
    assert updated.status_code == 200
    offers = (
        await client.get("/api/partner/offers", headers={"X-API-Key": raw})
    ).json()
    assert len(offers) == 1 and offers[0]["note"] == "Ещё дешевле"

    conflict = await client.post(
        "/api/partner/offers",
        json={"listing_id": listing.id, "note": "Мой лот"},
        headers={"X-API-Key": raw_other},
    )
    assert conflict.status_code == 409

    removed = await client.delete(
        f"/api/partner/offers/{listing.id}", headers={"X-API-Key": raw}
    )
    assert removed.status_code == 200
    assert (
        await client.delete(
            f"/api/partner/offers/{listing.id}", headers={"X-API-Key": raw}
        )
    ).status_code == 404


async def test_offer_missing_listing_404(
    client: AsyncClient, session: AsyncSession
):
    """Лота нет/неактивен — 404, витрина не меняется."""
    _, raw = await _make_partner(session)
    resp = await client.post(
        "/api/partner/offers",
        json={"listing_id": 999999999},
        headers={"X-API-Key": raw},
    )
    assert resp.status_code == 404


async def test_partner_endpoints_require_api_key(client: AsyncClient):
    """Без X-API-Key партнёрские зеркала недоступны (401)."""
    for method, path, kwargs in (
        ("get", "/api/partner/offers", {}),
        ("post", "/api/partner/offers", {"json": {"listing_id": 1}}),  # тело валидно → 401 от auth
        ("get", "/api/partner/chats", {}),
        ("get", "/api/partner/chats/ws-token", {}),
    ):
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 401, path


async def test_partner_chats_unread_read_and_ws_token(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    user,
):
    """Owner-лот партнёра связывает seller-тред: unread, read, ws-token.

    Seller-треды партнёра — по лотам рынка (owner_type='partner', 06 §5);
    витрина внешних лотов к чатам больше не привязана.
    """
    import jwt as pyjwt

    from app.config import get_settings

    account, raw = await create_partner(
        session,
        name="Рынок-чат",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100.00"),
        cpl_rate=None,
    )
    partner_id = account.id
    lot_id = await _partner_owner_lot(session, client, account, raw)

    # Тред на owner-лот: продавец = партнёр-владелец (§5)
    thread_resp = await client.post(
        f"/api/chats/seller/{lot_id}", headers=auth_headers
    )
    assert thread_resp.status_code == 201
    thread_id = thread_resp.json()["thread_id"]
    thread = (
        await session.execute(
            select(ChatThread).where(ChatThread.id == UUID(thread_id))
        )
    ).scalar_one()
    assert thread.partner_id == partner_id

    sent = await client.post(
        f"/api/chats/{thread_id}/messages",
        json={"body": "Здравствуйте, торг возможен?"},
        headers=auth_headers,
    )
    assert sent.status_code == 201

    chats = (await client.get("/api/partner/chats", headers={"X-API-Key": raw})).json()
    assert len(chats) == 1
    mine = chats[0]
    assert mine["id"] == thread_id and mine["type"] == "seller"
    # unread = все сообщения партнёра после last_read(0): system-приветствие + юзер
    assert mine["unread"] == 2
    assert mine["subject"]["id"] == lot_id

    messages = (
        await client.get(
            f"/api/partner/chats/{thread_id}/messages", headers={"X-API-Key": raw}
        )
    ).json()
    assert messages[-1]["body"] == "Здравствуйте, торг возможен?"

    read = await client.post(
        f"/api/partner/chats/{thread_id}/read", json={}, headers={"X-API-Key": raw}
    )
    assert read.status_code == 200
    chats = (await client.get("/api/partner/chats", headers={"X-API-Key": raw})).json()
    assert chats[0]["unread"] == 0

    # Юзер открыл чат и прочитал (фронт зовёт read при входе в ChatRoom)
    user_read = await client.post(
        f"/api/chats/{thread_id}/read", json={}, headers=auth_headers
    )
    assert user_read.status_code == 200

    # Партнёр пишет → у юзера непрочитанное в личном списке
    answer = await client.post(
        f"/api/partner/chats/{thread_id}/messages",
        json={"body": "Да, минус 500."},
        headers={"X-API-Key": raw},
    )
    assert answer.status_code == 201
    user_threads = (await client.get("/api/chats", headers=auth_headers)).json()
    user_thread = next(t for t in user_threads if t["id"] == thread_id)
    assert user_thread["unread"] == 1

    # ws-token кабинета: sub=partner:{id}
    token_resp = await client.get(
        "/api/partner/chats/ws-token", headers={"X-API-Key": raw}
    )
    assert token_resp.status_code == 200
    claims = pyjwt.decode(
        token_resp.json()["token"],
        get_settings().centrifugo_secret.get_secret_value(),
        algorithms=["HS256"],
    )
    assert claims["sub"] == f"partner:{partner_id}"


async def test_partner_cannot_read_foreign_thread(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    """Чужой партнёр не видит seller-тред даже с валидным ключом (404)."""
    owner_account, raw_owner = await create_partner(
        session,
        name="Рынок-владелец",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100.00"),
        cpl_rate=None,
    )
    _, raw_other = await _make_partner(session)
    # Owner-лот владельца — продавец треда (06 §5)
    lot_id = await _partner_owner_lot(session, client, owner_account, raw_owner)

    thread_resp = await client.post(
        f"/api/chats/seller/{lot_id}", headers=auth_headers
    )
    thread_id = thread_resp.json()["thread_id"]
    assert (
        await client.get(
            f"/api/partner/chats/{thread_id}/messages", headers={"X-API-Key": raw_other}
        )
    ).status_code == 404
    # Владелец витрины видит
    assert (
        await client.get(
            f"/api/partner/chats/{thread_id}/messages", headers={"X-API-Key": raw_owner}
        )
    ).status_code == 200


# =========================================================================
# Deal-треды в списке чатов: lead_id + lead_status (долг ревизора №6)
# =========================================================================


async def test_partner_chats_deal_thread_lead_fields(
    client: AsyncClient, session: AsyncSession, auth_headers: dict, user
):
    """Deal-тред отдаёт lead_id и partner_status лида: кабинет по ним
    решает, показывать ли CTA «Отметить сделкой»; после фидбека deal
    список отдаёт обновлённый статус."""
    partner_id, raw = await _make_partner(session)
    lead = Lead(
        user_id=user.telegram_id,
        source="bot",
        status="new",
        partner_id=partner_id,
        partner_status="claimed",
        claimed_at=datetime.now(UTC),
    )
    session.add(lead)
    await session.flush()
    session.add(
        ChatThread(
            type="deal",
            user_id=user.telegram_id,
            partner_id=partner_id,
            lead_id=lead.id,
        )
    )
    await session.flush()

    chats = (
        await client.get("/api/partner/chats", headers={"X-API-Key": raw})
    ).json()
    mine = next(t for t in chats if t["type"] == "deal")
    assert mine["lead_id"] == lead.id
    assert mine["lead_status"] == "claimed"

    # Фидбек «сделка» (CTA из чат-списка) меняет статус в выдаче
    feedback = await client.post(
        f"/api/partner/leads/{lead.id}/feedback",
        json={"status": "deal"},
        headers={"X-API-Key": raw},
    )
    assert feedback.status_code == 200
    chats = (
        await client.get("/api/partner/chats", headers={"X-API-Key": raw})
    ).json()
    mine = next(t for t in chats if t["type"] == "deal")
    assert mine["lead_status"] == "deal"


# =========================================================================
# Идемпотентность POST /api/partner/chats/{id}/messages (долг №7)
# =========================================================================


class _PublishCounter:
    """Мок ChatPublisher: запоминает каждый вызов publish."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def publish(self, channel: str, data: dict) -> None:
        self.calls.append((channel, dict(data)))


async def test_partner_send_message_idempotency_replay(
    session: AsyncSession,
    fake_redis,
    auth_headers: dict,
    user,
):
    """Партнёрский POST с одним Idempotency-Key: 201 → 200 с тем же
    message_id, публикация в канал треда ровно одна."""
    from app.main_api import app as fastapi_app

    counter = _PublishCounter()

    class _PublisherOverride(Provider):
        @provide(scope=Scope.APP)
        def chat_publisher(self) -> ChatPublisher:
            return counter

    partner_account, raw = await create_partner(
        session,
        name="Рынок-идем",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100.00"),
        cpl_rate=None,
    )
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
            lot_id = await _partner_owner_lot(session, http, partner_account, raw)
            thread_resp = await http.post(
                f"/api/chats/seller/{lot_id}", headers=auth_headers
            )
            assert thread_resp.status_code == 201, thread_resp.text
            thread_id = thread_resp.json()["thread_id"]
            headers = {"X-API-Key": raw, "Idempotency-Key": "partner-idem-1"}

            first = await http.post(
                f"/api/partner/chats/{thread_id}/messages",
                json={"body": "да, минус 500"},
                headers=headers,
            )
            assert first.status_code == 201
            message_id = first.json()["id"]

            second = await http.post(
                f"/api/partner/chats/{thread_id}/messages",
                json={"body": "да, минус 500"},
                headers=headers,
            )
            assert second.status_code == 200
            assert second.json()["id"] == message_id

            # 09 §1: личные каналы участников, каналов thread:* нет;
            # юзеру-владельцу и партнёру — по одному событию от первой
            # отправки (повтор не публикуется)
            assert not [c for c in counter.calls if c[0].startswith("thread:")]
            message_calls = [
                c for c in counter.calls if c[1]["type"] == "message"
            ]
            assert sorted(c[0] for c in message_calls) == [
                f"partner:{partner_account.id}",
                f"user:{user.telegram_id}",
            ]
            idem_keys = [k for k in fake_redis.data if k.startswith("chat:idem:partner:")]
            assert len(idem_keys) == 1
            assert fake_redis.data[idem_keys[0]] == str(message_id)
    finally:
        fastapi_app.state.dishka_container = prev_container
        await container.close()
