"""Лоты рынка с владельцем (06 §1-§5): placement-правила, пакеты, продление.

Сквозные API-тесты на тестовой БД (client-фикстура, как test_market.py):
размещение юзера (free-период / включённый / needs_payment + квота),
пакеты и слоты партнёра, продление через контур payments (kind='placement',
идемпотентность вебхука), гостевые 403 и seller-тред двух пользователей.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.db.models import (
    ChatParticipant,
    ChatThread,
    Listing,
    PartnerTransaction,
    Payment,
    User,
)
from tests.partners.helpers import create_partner

pytestmark = pytest.mark.asyncio

LOT_BODY = {
    "make": "Hyundai",
    "model": "Palisade",
    "year": 2022,
    "price": 38000,
    "currency": "USD",
    "mileage_km": 42000,
    "description": "Один владелец, сервис по регламенту",
    "photos": ["https://cdn.example.test/1.jpg"],
    # Гео (GEO-V1 §2): страна обязательна при создании owner-лота.
    "country": "RU",
}


def _lot_body(make: str = "Hyundai") -> dict:
    """Тело POST /api/market/lots (марку варьируем для читаемости БД)."""
    return {**LOT_BODY, "make": make}


async def _create_lot(
    client, headers: dict, make: str = "Hyundai", *, submit: bool = True
) -> dict:
    """POST /api/market/lots → тело ответа (статус 201 проверяет тест).

    По умолчанию submit=True (10 §4): создаём и сразу отправляем на
    модерацию — так сохранилась семантика «разместил → опубликован»
    прежних тестов placement-правил.
    """
    resp = await client.post(
        "/api/market/lots",
        json={**_lot_body(make), "submit": submit},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_history(
    session: AsyncSession, user_id: int, *, source_id: str = "hist-1"
) -> Listing:
    """Исторический accepted-лот юзера (продан давно).

    Жизненный цикл (10 §2): владелец без ни одного accepted-лота —
    new_user → ручная модерация. История (старый лот) снимает правило.
    Лот неактивен с истёкшим free-датой: не занимает квоту платных
    (count_paid_pending_lots) и не мешает кулдауну (first_seen 40 дней).
    """
    stale = datetime.now(UTC) - timedelta(days=40)
    listing = Listing(
        source="market",
        source_id=source_id,
        url=f"/listing/market/{source_id}",
        make="Oldsmobile",
        model="Ride",
        year=2010,
        price=Decimal("1000"),
        currency="USD",
        owner_type="user",
        owner_user_id=user_id,
        is_active=False,
        status="accepted",
        placement_free_until=stale,
        first_seen_at=stale,
        parsed_at=stale,
    )
    session.add(listing)
    await session.flush()
    return listing


async def _get_lot(session: AsyncSession, listing_id: int) -> Listing:
    """Свежая строка лота из БД."""
    await session.refresh(await session.get(Listing, listing_id))
    return await session.get(Listing, listing_id)


# =========================================================================
# (а) Free-тариф: первый лот — бесплатный период 7 дней
# =========================================================================


async def test_free_user_first_lot_gets_free_period(
    client, auth_headers: dict, session: AsyncSession, user: User
):
    """submit=true: accepted + free (free_until≈+7d, активен, user-owner)."""
    # История продавца (10 §2): иначе new_user → ручная модерация.
    await _seed_history(session, user.telegram_id)
    before = datetime.now(UTC)
    created = await _create_lot(client, auth_headers)

    assert created == {
        "listing_id": created["listing_id"],
        "placement_status": "free",
        "is_active": True,
        "needs_payment": False,
        "status": "accepted",
        # Качество объявления (12 §2): 1 фото и короткое описание.
        "quality": {
            "score": 55,
            "tips": [
                "Добавьте фото кузова и салона (минимум 3)",
                "Дополните описание — минимум 80 знаков",
            ],
        },
    }
    lot = await _get_lot(session, created["listing_id"])
    assert lot.owner_type == "user"
    assert lot.owner_user_id == user.telegram_id
    assert lot.owner_partner_id is None
    assert lot.source == "market"
    assert lot.url == f"/listing/{lot.id}"
    assert lot.is_active is True
    assert lot.status == "accepted"
    assert lot.placement_paid_until is None
    # Бесплатный период ≈ 7 дней от создания (не меньше, с точностью до минут)
    free_until = lot.placement_free_until.replace(tzinfo=UTC)
    assert timedelta(days=6, hours=23) < free_until - before < timedelta(
        days=7, minutes=5
    )

    # «Мои объявления»: тот же статус и дедлайн (история тоже в списке)
    my = (await client.get("/api/market/my-lots", headers=auth_headers)).json()
    mine = [item for item in my if item["listing_id"] == lot.id]
    assert len(mine) == 1
    assert mine[0]["placement_status"] == "free"
    assert mine[0]["is_active"] is True
    assert mine[0]["status"] == "accepted"


async def test_market_lot_requires_registration(
    client, guest_headers: dict
):
    """Гость не размещает лоты: 403 market_registration_required (§2)."""
    resp = await client.post(
        "/api/market/lots", json=_lot_body(), headers=guest_headers
    )
    assert resp.status_code == 403
    assert "market_registration_required" in resp.json()["detail"]


# =========================================================================
# (б) Доп. лоты free-тарифа: needs_payment-201 до квоты, затем 409
# =========================================================================


async def test_free_user_extra_lots_wait_payment_until_quota(
    client, auth_headers: dict, session: AsyncSession, user: User
):
    """Второй+ лоты — 201 needs_payment (is_active=false); квота
    max_user_paid_lots=3 исчерпана → 409 market_limit_reached (§3)."""
    await _seed_history(session, user.telegram_id)
    first = await _create_lot(client, auth_headers)
    assert first["placement_status"] == "free"

    # Три доп. лота укладываются в квоту: создаются под оплату (201)
    for i in range(3):
        extra = await _create_lot(client, auth_headers, make=f"Kia{i}")
        assert extra["needs_payment"] is True
        assert extra["is_active"] is False
        assert extra["placement_status"] == "needs_payment"
        assert extra["status"] == "accepted"  # одобрен, но скрыт до оплаты
        lot = await _get_lot(session, extra["listing_id"])
        assert lot.is_active is False
        assert lot.placement_free_until is None
        assert lot.placement_paid_until is None

    # Четвёртый доп. лот — за квотой: 409 с апселлом
    over = await client.post(
        "/api/market/lots", json=_lot_body("BMW"), headers=auth_headers
    )
    assert over.status_code == 409
    assert "market_limit_reached" in over.json()["detail"]

    # Снять с продажи: needs_payment-лот закрывается навсегда (слот дат нет)
    removed = await client.delete(
        f"/api/market/lots/{first['listing_id']}", headers=auth_headers
    )
    assert removed.status_code == 200
    assert (
        await client.delete(
            f"/api/market/lots/{first['listing_id']}", headers=auth_headers
        )
    ).status_code == 404


# =========================================================================
# (б2) Аудит 06 §3: кулдаун бесплатного периода (анти-ротация)
# =========================================================================


async def test_free_period_cooldown_blocks_rotation(
    client, auth_headers: dict, session: AsyncSession, user: User
):
    """Ротация «лот истёк → новый лот» не даёт новую бесплатную неделю:
    free-период — не чаще раза в market_free_cooldown_days (по
    first_seen_at всех лотов юзера, включая снятые/истёкшие)."""
    await _seed_history(session, user.telegram_id)
    first = await _create_lot(client, auth_headers)
    assert first["placement_status"] == "free"

    # Лот истёк и снят экспиратором: is_active=false, free_until в прошлом
    expired = await _get_lot(session, first["listing_id"])
    expired.is_active = False
    expired.placement_free_until = datetime.now(UTC) - timedelta(days=1)
    await session.flush()

    # Активных лотов нет, но лоты за последние 30 дней есть → платно
    second = await _create_lot(client, auth_headers, make="Kia")
    assert second["needs_payment"] is True
    assert second["placement_status"] == "needs_payment"
    second_lot = await _get_lot(session, second["listing_id"])
    assert second_lot.placement_free_until is None  # бесплатной недели нет

    # Кулдаун прошёл (оба лота старше 30 дней) → снова free-период
    stale = datetime.now(UTC) - timedelta(days=31)
    expired.first_seen_at = stale
    second_lot.first_seen_at = stale
    second_lot.is_active = False
    await session.flush()

    third = await _create_lot(client, auth_headers, make="BMW")
    assert third["placement_status"] == "free"
    assert third["is_active"] is True
    third_lot = await _get_lot(session, third["listing_id"])
    assert third_lot.placement_free_until is not None


# =========================================================================
# (в) Pro-тариф: первый лот включён (без placement-дат)
# =========================================================================


async def test_pro_user_first_lot_is_included(
    client, session: AsyncSession
):
    """Включённый лот платного тарифа: без дат, активен (§3)."""
    pro = User(
        telegram_id=900101,
        username="fixture_pro_seller",
        role="buyer",
        subscription_tier="pro",
        subscription_until=datetime.now(UTC) + timedelta(days=30),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(pro)
    await session.flush()
    # История продавца: без неё new_user → ручная модерация (10 §2).
    await _seed_history(session, pro.telegram_id, source_id="hist-pro")
    from app.infrastructure.security.tokens import create_access_token

    headers = {
        "Authorization": f"Bearer {create_access_token(pro.telegram_id, get_settings())}"
    }

    created = await _create_lot(client, headers)
    assert created["placement_status"] == "included"
    assert created["is_active"] is True
    assert created["needs_payment"] is False
    assert created["status"] == "accepted"

    lot = await _get_lot(session, created["listing_id"])
    assert lot.is_active is True
    assert lot.placement_free_until is None
    assert lot.placement_paid_until is None


# =========================================================================
# (г) Партнёр: пакеты размещений + лоты за слоты
# =========================================================================


async def _partner(session: AsyncSession, balance: Decimal, name: str):
    """Партнёр с балансом: (account, raw_key)."""
    return await create_partner(
        session,
        name=name,
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=balance,
        cpl_rate=None,
    )


async def test_market_packages_listed_and_bought(
    client, session: AsyncSession
):
    """GET: пакеты из конфига; POST: атомарно balance-=price, slots+=size,
    partner_tx kind='adjustment' (§2). Не хватает баланса — 402."""
    account, raw = await _partner(session, Decimal("1000.00"), "Пакеты")

    listed = (
        await client.get("/api/partner/market/packages", headers={"X-API-Key": raw})
    ).json()
    assert listed == [
        {"size": 10, "price_rub": 500.0},
        {"size": 50, "price_rub": 1800.0},
        {"size": 200, "price_rub": 5000.0},
    ]

    bought = await client.post(
        "/api/partner/market/packages",
        json={"size": 10},
        headers={"X-API-Key": raw},
    )
    assert bought.status_code == 201, bought.text
    # PackageOut: размер/цена + slots_free — остаток мест после покупки
    assert bought.json() == {"size": 10, "price_rub": 500.0, "slots_free": 10}

    await session.refresh(account)
    assert account.balance == Decimal("500.00")
    assert account.placement_slots == 10
    assert account.placement_slots_used == 0
    tx = (
        await session.execute(
            select(PartnerTransaction)
            .where(PartnerTransaction.partner_id == account.id)
            .order_by(PartnerTransaction.id.desc())
        )
    ).scalars().first()
    assert tx.amount == Decimal("-500.00")
    assert tx.kind == "adjustment"
    assert tx.comment == "placement package 10"

    # Нет такого размера пакета — 404
    unknown = await client.post(
        "/api/partner/market/packages",
        json={"size": 7},
        headers={"X-API-Key": raw},
    )
    assert unknown.status_code == 404
    assert "package_not_found" in unknown.json()["detail"]

    # Баланс 100 ₽ < 1800 ₽ → 402 insufficient_balance
    poor_account, poor_raw = await _partner(session, Decimal("100.00"), "Бедный")
    rejected = await client.post(
        "/api/partner/market/packages",
        json={"size": 50},
        headers={"X-API-Key": poor_raw},
    )
    assert rejected.status_code == 402
    assert "insufficient_balance" in rejected.json()["detail"]
    await session.refresh(poor_account)
    assert poor_account.balance == Decimal("100.00")  # ничего не списано
    assert poor_account.placement_slots == 0


async def test_partner_lot_consumes_slot(
    client, session: AsyncSession, monkeypatch
):
    """POST /api/partner/market/lots: слот расходуется (slots_used+1),
    лот бессрочный (без placement-дат); слотов нет — 409 (§2)."""
    from app.infrastructure.db.models import PartnerAccount

    # Сэмплинг партнёров (10 §2): фиксируем random>0.1 — авто-accept.
    from unittest.mock import patch

    account, raw = await _partner(session, Decimal("100.00"), "Слоты")
    account.placement_slots = 1
    await session.flush()
    partner_id = account.id

    with patch(
        "app.application.market.moderation.random"
    ) as sampled:
        sampled.random.return_value = 0.9
        created = await client.post(
            "/api/partner/market/lots",
            json=_lot_body("Genesis"),
            headers={"X-API-Key": raw},
        )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["placement_status"] == "slot"
    assert body["is_active"] is True
    assert body["needs_payment"] is False
    assert body["status"] == "accepted"  # партнёрский submit авто (10 §4)

    lot = await _get_lot(session, body["listing_id"])
    assert lot.owner_type == "partner"
    assert lot.owner_partner_id == partner_id
    assert lot.placement_free_until is None
    assert lot.placement_paid_until is None
    assert lot.status == "accepted"

    fresh = await session.get(PartnerAccount, partner_id)
    assert fresh.placement_slots_used == 1  # слот израсходован

    # Свободных слотов нет → 409 placement_slots_exhausted
    exhausted = await client.post(
        "/api/partner/market/lots",
        json=_lot_body("Kia"),
        headers={"X-API-Key": raw},
    )
    assert exhausted.status_code == 409
    assert "placement_slots_exhausted" in exhausted.json()["detail"]

    # Список размещений партнёра
    lots = (
        await client.get("/api/partner/market/lots", headers={"X-API-Key": raw})
    ).json()
    assert [item["listing_id"] for item in lots] == [body["listing_id"]]
    assert lots[0]["placement_status"] == "slot"

    # Снятие: слот не возвращается (статус archived, 10 §1)
    removed = await client.delete(
        f"/api/partner/market/lots/{body['listing_id']}",
        headers={"X-API-Key": raw},
    )
    assert removed.status_code == 200
    closed = await _get_lot(session, body["listing_id"])
    assert closed.status == "archived"
    assert closed.is_active is False
    assert (
        await session.get(PartnerAccount, partner_id)
    ).placement_slots_used == 1


# =========================================================================
# (д) Продление размещения: инвойс Stars → вебхук → paid_until
# =========================================================================


@pytest.fixture
def invoice_env(monkeypatch):
    """BOT_TOKEN + стаб createInvoiceLink (Telegram не дёргаем)."""
    from app.transport.http.routers import market as market_api

    settings = get_settings()
    monkeypatch.setattr(settings, "bot_token", SecretStr("123:test"))

    async def fake_link(**kwargs):
        return "https://t.me/$placementinvoice"

    monkeypatch.setattr(market_api, "_create_invoice_link", fake_link)
    return settings


async def test_extend_lot_invoice_and_payment_webhook(
    client,
    auth_headers: dict,
    session: AsyncSession,
    user: User,
    invoice_env,
    internal_token: str,
):
    """Инвойс kind='placement' (20★/нед); вебхук продлевает paid_until
    на 7d*weeks от max(now, coalesce(paid, free)); повтор — идемпотентен."""
    # История продавца: лот проходит модерацию и получает free-период.
    await _seed_history(session, user.telegram_id, source_id="hist-ext")
    created = await _create_lot(client, auth_headers)
    listing_id = created["listing_id"]

    invoice = await client.post(
        f"/api/market/lots/{listing_id}/extend",
        json={"weeks": 1},
        headers=auth_headers,
    )
    assert invoice.status_code == 200, invoice.text
    body = invoice.json()
    assert body["amount"] == 20  # market_week_stars
    assert body["weeks"] == 1
    assert body["listing_id"] == listing_id
    assert body["currency"] == "XTR"
    assert body["invoice_url"].startswith("https://t.me/")

    payment = await session.get(Payment, body["payment_id"])
    assert payment.kind == "placement"
    assert payment.status == "pending"
    assert payment.listing_id == listing_id
    assert payment.period_days == 7
    assert payment.amount == Decimal("20")

    # successful_payment (internal, как бот): lot оживёт и продлится
    confirm = await client.post(
        "/api/payments/success",
        json={
            "payment_id": body["payment_id"],
            "user_id": user.telegram_id,
            "provider_payment_id": "tg-charge-1",
            "amount": 20,
        },
        headers={"X-Internal-Token": internal_token},
    )
    assert confirm.status_code == 200, confirm.text

    lot = await _get_lot(session, listing_id)
    paid_until = lot.placement_paid_until.replace(tzinfo=UTC)
    # base = max(now, free_until≈now+7d) + 7d ≈ now+14d
    assert timedelta(days=13, hours=23) < paid_until - datetime.now(
        UTC
    ) < timedelta(days=14, hours=1)
    assert lot.is_active is True

    # Повторная доставка вебхука: paid-статус уже отработан, не продлевает
    replay = await client.post(
        "/api/payments/success",
        json={
            "payment_id": body["payment_id"],
            "user_id": user.telegram_id,
            "provider_payment_id": "tg-charge-1",
            "amount": 20,
        },
        headers={"X-Internal-Token": internal_token},
    )
    assert replay.status_code == 200
    replay_lot = await _get_lot(session, listing_id)
    assert replay_lot.placement_paid_until.replace(tzinfo=UTC) == paid_until


async def test_extend_lot_foreign_or_missing(
    client, auth_headers: dict, session: AsyncSession, invoice_env
):
    """Чужой/несуществующий лот — 404; окно не 1/4 — 422 (Literal)."""
    other = User(
        telegram_id=900102,
        username="fixture_other_seller",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(other)
    await session.flush()
    from app.infrastructure.security.tokens import create_access_token

    foreign_headers = {
        "Authorization": f"Bearer {create_access_token(other.telegram_id, get_settings())}"
    }
    mine = await _create_lot(client, foreign_headers)

    resp = await client.post(
        f"/api/market/lots/{mine['listing_id']}/extend",
        json={"weeks": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 404

    missing = await client.post(
        "/api/market/lots/999999999/extend",
        json={"weeks": 1},
        headers=auth_headers,
    )
    assert missing.status_code == 404

    bad_window = await client.post(
        f"/api/market/lots/{mine['listing_id']}/extend",
        json={"weeks": 2},
        headers=foreign_headers,
    )
    assert bad_window.status_code == 422


# =========================================================================
# (ё) Чаты: гость — 403 на всех эндпоинтах; user-лот — тред двух юзеров
# =========================================================================


async def test_guest_blocked_on_all_chat_endpoints(
    client, guest_headers: dict
):
    """Гость не проходит ни на один /api/chats-эндпоинт (06 §5)."""
    thread_url = "/api/chats/00000000-0000-0000-0000-000000000000"

    ws = await client.get("/api/chats/ws-token", headers=guest_headers)
    assert ws.status_code == 403
    assert "chats_registration_required" in ws.json()["detail"]

    listing_threads = await client.get("/api/chats", headers=guest_headers)
    assert listing_threads.status_code == 403
    support = await client.post("/api/chats/support", headers=guest_headers)
    assert support.status_code == 403
    seller = await client.post("/api/chats/seller/1", headers=guest_headers)
    assert seller.status_code == 403
    messages = await client.get(f"{thread_url}/messages", headers=guest_headers)
    assert messages.status_code == 403
    sent = await client.post(
        f"{thread_url}/messages", json={"body": "привет"}, headers=guest_headers
    )
    assert sent.status_code == 403
    read = await client.post(f"{thread_url}/read", json={}, headers=guest_headers)
    assert read.status_code == 403
    preview = await client.get(
        f"{thread_url}/link-preview",
        params={"url": "https://example.test"},
        headers=guest_headers,
    )
    assert preview.status_code == 403


async def test_seller_chat_user_lot_creates_two_user_thread(
    client, auth_headers: dict, session: AsyncSession, user: User
):
    """Owner_type='user': тред двух пользователей — владелец получает
    строку chat_participants и видит тред в своём списке (§5)."""
    owner_headers = auth_headers
    # История продавца: лот публикуется сразу (accepted + is_active).
    await _seed_history(session, user.telegram_id, source_id="hist-chat")
    created = await _create_lot(client, owner_headers)
    listing_id = created["listing_id"]

    buyer = User(
        telegram_id=900103,
        username="fixture_buyer_chat",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(buyer)
    await session.flush()
    from app.infrastructure.security.tokens import create_access_token

    buyer_headers = {
        "Authorization": f"Bearer {create_access_token(buyer.telegram_id, get_settings())}"
    }

    thread_resp = await client.post(
        f"/api/chats/seller/{listing_id}", headers=buyer_headers
    )
    assert thread_resp.status_code == 201
    thread_id = UUID(thread_resp.json()["thread_id"])

    # Тред пользователей: partner_id нет; владелец — вторая строка участника
    thread = (
        await session.execute(
            select(ChatThread).where(ChatThread.id == thread_id)
        )
    ).scalar_one()
    assert thread.type == "seller"
    assert thread.user_id == buyer.telegram_id
    assert thread.partner_id is None

    participant = (
        await session.execute(
            select(ChatParticipant).where(ChatParticipant.thread_id == thread_id)
        )
    ).scalars().one()
    assert participant.user_type == "user"
    assert participant.user_id == user.telegram_id

    # Владелец видит тред в своём списке (по участию, §2)
    owner_threads = (
        await client.get("/api/chats", headers=owner_headers)
    ).json()
    assert any(t["id"] == str(thread_id) for t in owner_threads)
