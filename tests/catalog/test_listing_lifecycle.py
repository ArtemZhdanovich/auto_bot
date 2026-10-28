"""Жизненный цикл объявления рынка (редизайн часть 10, §1-§4).

Сквозные API-тесты на тестовой БД: черновик (не в ленте, виден в
my-lots), submit → авто-модерация (accept/manual/reject: контакты,
запрещённые слова, new_user, выброс цены, дубликат, партнёрский
сэмплинг), версионность правок опубликованного (pending_patch +
решения админа), очередь модерации, экспирация (см. test_send_worker)
и события listing_status в личный канал centrifugo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.db.models import Listing, User
from app.infrastructure.security.tokens import create_access_token
from app.infrastructure.services.centrifugo import CentrifugoPublisher
from tests.partners.helpers import create_partner

pytestmark = pytest.mark.asyncio

# Нормальное описание: >=30 символов, без контактов и запрещённых слов.
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


def _body(**overrides) -> dict:
    """Тело POST /api/market/lots с переопределениями."""
    return {**LOT_BODY, **overrides}


async def _make_seller(
    session: AsyncSession, telegram_id: int, username: str
) -> tuple[User, dict[str, str]]:
    """Свободный юзер + Bearer-заголовки."""
    seller = User(
        telegram_id=telegram_id,
        username=username,
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(seller)
    await session.flush()
    headers = {
        "Authorization": f"Bearer {create_access_token(telegram_id, get_settings())}"
    }
    return seller, headers


async def _seed_history(
    session: AsyncSession, user_id: int, *, source_id: str
) -> Listing:
    """Исторический accepted-лот юзера (10 §2: снимает правило new_user).

    Неактивен с истёкшей датой: не занимает квоту платных лотов и не
    попадает в кулдаун бесплатного периода (first_seen 40 дней назад).
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


async def _create_draft(
    client: AsyncClient, headers: dict, *, source_id_note: str = "", **overrides
) -> int:
    """POST /api/market/lots без submit → id черновика."""
    resp = await client.post(
        "/api/market/lots", json=_body(**overrides), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert body["placement_status"] == "none"
    assert body["is_active"] is False
    return body["listing_id"]


async def _submit(
    client: AsyncClient, headers: dict, listing_id: int
) -> dict:
    """POST /api/market/lots/{id}/submit → тело ответа."""
    resp = await client.post(
        f"/api/market/lots/{listing_id}/submit", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _feed_ids(client: AsyncClient) -> set[int]:
    """Id лотов чипса «🏪 Рынок» (source=market)."""
    resp = await client.get("/api/listings", params={"source": "market"})
    assert resp.status_code == 200, resp.text
    return {item["id"] for item in resp.json()["items"]}


async def _pdp(client: AsyncClient, listing_id: int):
    """GET /api/listings/{id} (PDP)."""
    return await client.get(f"/api/listings/{listing_id}")


async def _my_lots(client: AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/market/my-lots", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _lot_in(mylots: list[dict], listing_id: int) -> dict:
    """Свежая карточка лота из my-lots."""
    found = [item for item in mylots if item["listing_id"] == listing_id]
    assert found, f"лот {listing_id} отсутствует в my-lots"
    return found[0]


def _events(spy, channel: str, listing_id: int) -> list[dict]:
    """События listing_status лота из канала."""
    return [
        data
        for ch, data in spy.calls
        if ch == channel
        and data.get("type") == "listing_status"
        and data.get("listing_id") == listing_id
    ]


# --- мок centrifugo: перехват publish на уровне адаптера ----------------------


class _PublishSpy:
    """Собирает (channel, data) всех публикаций centrifugo."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, channel: str, data: dict) -> None:
        self.calls.append((channel, dict(data)))


@pytest.fixture
def centrifugo_spy(monkeypatch) -> _PublishSpy:
    """Подменить CentrifugoPublisher.publish (все инстансы DI)."""
    spy = _PublishSpy()
    monkeypatch.setattr(CentrifugoPublisher, "publish", spy)
    return spy


# =========================================================================
# (1) Черновик: не в ленте/PDP, виден в my-lots (10 §1, §4)
# =========================================================================


async def test_draft_hidden_from_feed_but_in_my_lots(
    client: AsyncClient, session: AsyncSession, centrifugo_spy: _PublishSpy
):
    """POST без submit → draft: is_active=false без дат, не в ленте,
    PDP 404; в my-lots со статусом draft и placement none."""
    seller, headers = await _make_seller(session, 901101, "lc_seller_draft")
    listing_id = await _create_draft(client, headers)

    lot = await session.get(Listing, listing_id)
    assert lot.status == "draft"
    assert lot.is_active is False
    assert lot.placement_free_until is None
    assert lot.placement_paid_until is None

    # Лента и PDP черновик не видят
    assert listing_id not in await _feed_ids(client)
    pdp = await _pdp(client, listing_id)
    assert pdp.status_code == 404

    # my-lots показывает
    mine = _lot_in(await _my_lots(client, headers), listing_id)
    assert mine["status"] == "draft"
    assert mine["placement_status"] == "none"

    # Событий нет: статус не менялся (создание ≠ переход автомата)
    assert centrifugo_spy.calls == []


# =========================================================================
# (2) Submit → accept: risk 0, история продавца, free-период + событие
# =========================================================================


async def test_submit_accept_grants_free_period(
    client: AsyncClient, session: AsyncSession, centrifugo_spy: _PublishSpy
):
    """Юзер с историей и чистым лотом → accepted + free_until≈+7d; лот
    появляется в ленте; событие listing_status уходит в user:{owner}."""
    seller, headers = await _make_seller(session, 901102, "lc_seller_ok")
    await _seed_history(session, seller.telegram_id, source_id="lc-hist-ok")
    before = datetime.now(UTC)
    listing_id = await _create_draft(client, headers)

    result = await _submit(client, headers, listing_id)
    assert result["status"] == "accepted"
    assert result["placement_status"] == "free"
    assert result["is_active"] is True
    assert result["needs_payment"] is False
    assert result["violations"] == []

    lot = await session.get(Listing, listing_id)
    assert lot.status == "accepted"
    free_until = lot.placement_free_until.replace(tzinfo=UTC)
    assert timedelta(days=6, hours=23) < free_until - before < timedelta(
        days=7, minutes=5
    )
    assert listing_id in await _feed_ids(client)

    events = _events(centrifugo_spy, f"user:{seller.telegram_id}", listing_id)
    assert events == [
        {"type": "listing_status", "listing_id": listing_id, "status": "accepted"}
    ]


# =========================================================================
# (3)-(4) Hard-правила: контакты и запрещённые слова → rejected
# =========================================================================


async def test_contacts_in_description_rejected_then_fixed(
    client: AsyncClient, session: AsyncSession, centrifugo_spy: _PublishSpy
):
    """Телефон в описании → rejected с reason; правка + повторный submit
    → accepted (rejected → moderation → accepted, 10 §1)."""
    seller, headers = await _make_seller(session, 901103, "lc_seller_phone")
    await _seed_history(session, seller.telegram_id, source_id="lc-hist-phone")
    listing_id = await _create_draft(
        client, headers, description="Звоните +7 999 123-45-67, обсудим цену"
    )

    result = await _submit(client, headers, listing_id)
    assert result["status"] == "rejected"
    assert "contacts_in_text" in result["rejected_reason"]
    assert [v["rule"] for v in result["violations"]] == ["contacts_in_text"]

    lot = await session.get(Listing, listing_id)
    assert lot.status == "rejected"
    assert lot.rejected_reason and "contacts_in_text" in lot.rejected_reason
    assert listing_id not in await _feed_ids(client)

    # my-lots: причина отказа + сработавшие правила
    mine = _lot_in(await _my_lots(client, headers), listing_id)
    assert mine["status"] == "rejected"
    assert "contacts_in_text" in mine["rejected_reason"]
    assert [v["rule"] for v in mine["violations"]] == ["contacts_in_text"]

    # «Исправить»: PATCH rejected-лота напрямую, повторный submit
    patched = await client.patch(
        f"/api/market/lots/{listing_id}",
        json={"description": "Свяжитесь через чат площадки, торг уместен"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "rejected"  # статус сменился submit'ом
    resubmit = await _submit(client, headers, listing_id)
    assert resubmit["status"] == "accepted"

    events = _events(centrifugo_spy, f"user:{seller.telegram_id}", listing_id)
    assert [e["status"] for e in events] == ["rejected", "accepted"]
    assert events[0]["reason"] and "contacts_in_text" in events[0]["reason"]


async def test_forbidden_word_rejected(
    client: AsyncClient, session: AsyncSession
):
    """Слово из market_forbidden_words (конфиг) → rejected (+40, hard)."""
    seller, headers = await _make_seller(session, 901104, "lc_seller_word")
    await _seed_history(session, seller.telegram_id, source_id="lc-hist-word")
    listing_id = await _create_draft(
        client, headers, description="Отличные инвестиции: машина окупится"
    )

    result = await _submit(client, headers, listing_id)
    assert result["status"] == "rejected"
    assert "forbidden_words" in result["rejected_reason"]
    lot = await session.get(Listing, listing_id)
    assert lot.status == "rejected"


# =========================================================================
# (5) new_user → ручная модерация; админ approve публикует
# =========================================================================


async def test_new_user_manual_moderation_and_admin_approve(
    client: AsyncClient,
    session: AsyncSession,
    internal_token: str,
    centrifugo_spy: _PublishSpy,
):
    """Первый лот владельца без истории → moderation (new_user, 10 §2):
    не в ленте, правки 409, повторный submit 409; админ approve →
    accepted + free-период (placement решается на approve)."""
    seller, headers = await _make_seller(session, 901105, "lc_seller_new")
    listing_id = await _create_draft(client, headers)

    result = await _submit(client, headers, listing_id)
    assert result["status"] == "moderation"
    assert result["is_active"] is False
    assert {v["rule"] for v in result["violations"]} == {"new_user"}

    lot = await session.get(Listing, listing_id)
    assert lot.status == "moderation"
    assert listing_id not in await _feed_ids(client)

    # Правки и повторный submit в очереди запрещены (10 §1)
    edit = await client.patch(
        f"/api/market/lots/{listing_id}",
        json={"price": 37000},
        headers=headers,
    )
    assert edit.status_code == 409
    assert "lot_invalid_state" in edit.json()["detail"]
    again = await client.post(
        f"/api/market/lots/{listing_id}/submit", headers=headers
    )
    assert again.status_code == 409

    # Очередь админа: лот на проверке с риском new_user
    queue = await client.get(
        "/api/admin/moderation",
        params={"queue": "moderation"},
        headers={"X-Internal-Token": internal_token},
    )
    assert queue.status_code == 200, queue.text
    items = [item for item in queue.json() if item["listing_id"] == listing_id]
    assert len(items) == 1
    assert items[0]["owner_type"] == "user"
    assert items[0]["risk_score"] == 25
    assert [v["rule"] for v in items[0]["violations"]] == ["new_user"]

    # Approve: placement-правила применяются здесь (первый лот → free)
    approved = await client.post(
        f"/api/admin/moderation/{listing_id}/approve",
        headers={"X-Internal-Token": internal_token},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json() == {
        "listing_id": listing_id,
        "status": "accepted",
        "placement_status": "free",
        "reason": None,
    }
    fresh = await session.get(Listing, listing_id)
    assert fresh.status == "accepted"
    assert fresh.is_active is True
    assert fresh.placement_free_until is not None
    assert listing_id in await _feed_ids(client)

    events = _events(centrifugo_spy, f"user:{seller.telegram_id}", listing_id)
    assert [e["status"] for e in events] == ["moderation", "accepted"]

    # Очередь опустела
    queue_after = await client.get(
        "/api/admin/moderation",
        headers={"X-Internal-Token": internal_token},
    )
    assert all(
        item["listing_id"] != listing_id for item in queue_after.json()
    )


# =========================================================================
# (6) price_outlier → manual
# =========================================================================


async def test_price_outlier_goes_manual(
    client: AsyncClient, session: AsyncSession, internal_token: str
):
    """Цена < 10% средней по той же модели (год ±2, comps) → violation
    price_outlier (+20) и ручная очередь (10 §2)."""
    seller, headers = await _make_seller(session, 901106, "lc_seller_cheap")
    await _seed_history(session, seller.telegram_id, source_id="lc-hist-cheap")
    # Comps: два внешних accepted-лота той же модели (год ±2), по 30000.
    for i, year in enumerate((2021, 2023)):
        session.add(
            Listing(
                source="encar",
                source_id=f"lc-comp-{i}",
                url=f"https://encar.test/lc-comp-{i}",
                make="Hyundai",
                model="Palisade",
                year=year,
                price=Decimal("30000"),
                currency="USD",
                parsed_at=datetime.now(UTC),
            )
        )
    await session.flush()

    listing_id = await _create_draft(client, headers, price=1000)
    result = await _submit(client, headers, listing_id)
    assert result["status"] == "moderation"
    assert [v["rule"] for v in result["violations"]] == ["price_outlier"]

    queue = await client.get(
        "/api/admin/moderation",
        headers={"X-Internal-Token": internal_token},
    )
    item = next(
        item for item in queue.json() if item["listing_id"] == listing_id
    )
    assert item["risk_score"] == 20


# =========================================================================
# (7) duplicate → manual
# =========================================================================


async def test_duplicate_lot_goes_manual(
    client: AsyncClient, session: AsyncSession
):
    """Тот же owner+make+model+year+price за 7 дней → duplicate (+35,
    manual) — без VIN ключ слабый, даём ручную проверку (§7)."""
    seller, headers = await _make_seller(session, 901107, "lc_seller_dup")
    await _seed_history(session, seller.telegram_id, source_id="lc-hist-dup")

    first = await _create_draft(client, headers)
    assert (await _submit(client, headers, first))["status"] == "accepted"

    second = await _create_draft(client, headers)
    result = await _submit(client, headers, second)
    assert result["status"] == "moderation"
    assert "duplicate" in [v["rule"] for v in result["violations"]]


# =========================================================================
# (8) Партнёр: авто-accept + 10% сэмплинг → manual
# =========================================================================


async def test_partner_lot_auto_accept_and_sampling(
    client: AsyncClient, session: AsyncSession, centrifugo_spy: _PublishSpy
):
    """Партнёрский submit авто (10 §4): random≥0.1 → accepted; выборка
    random<0.1 → moderation (201 со статусом), событие в partner:{id}."""
    account, raw = await create_partner(
        session,
        name="Модерация-сэмпл",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100"),
        cpl_rate=None,
    )
    account.placement_slots = 2
    await session.flush()

    with patch("app.application.market.moderation.random") as sampled:
        sampled.random.return_value = 0.9  # вне выборки
        ok = await client.post(
            "/api/partner/market/lots",
            json=_body(make="Genesis"),
            headers={"X-API-Key": raw},
        )
    assert ok.status_code == 201, ok.text
    assert ok.json()["status"] == "accepted"
    accepted_id = ok.json()["listing_id"]

    with patch("app.application.market.moderation.random") as sampled:
        sampled.random.return_value = 0.05  # попал в 10% выборки
        sampled_resp = await client.post(
            "/api/partner/market/lots",
            json=_body(make="Kia"),
            headers={"X-API-Key": raw},
        )
    assert sampled_resp.status_code == 201, sampled_resp.text
    assert sampled_resp.json()["status"] == "moderation"
    sampled_id = sampled_resp.json()["listing_id"]

    lot = await session.get(Listing, sampled_id)
    assert lot.status == "moderation"
    assert lot.is_active is True  # слот оплачен, но лента скрыта статусом
    assert sampled_id not in await _feed_ids(client)
    assert accepted_id in await _feed_ids(client)

    events = _events(centrifugo_spy, f"partner:{account.id}", sampled_id)
    assert [e["status"] for e in events] == ["moderation"]


# =========================================================================
# (9) PATCH accepted → pending_patch; решения админа
# =========================================================================


async def _published_lot(
    client: AsyncClient, session: AsyncSession, seller: User, headers: dict
) -> int:
    """Опубликованный accepted-лот юзера (история + submit → free)."""
    await _seed_history(
        session, seller.telegram_id, source_id=f"hist-{seller.telegram_id}"
    )
    listing_id = await _create_draft(client, headers)
    result = await _submit(client, headers, listing_id)
    assert result["status"] == "accepted"
    return listing_id


async def test_patch_accepted_creates_pending_version(
    client: AsyncClient, session: AsyncSession, internal_token: str
):
    """PATCH accepted → pending_patch (лента продолжает показывать
    старую цену); очередь patch у админа видит правку (10 §1)."""
    seller, headers = await _make_seller(session, 901108, "lc_seller_patch")
    listing_id = await _published_lot(client, session, seller, headers)

    patched = await client.patch(
        f"/api/market/lots/{listing_id}",
        json={"price": 35000, "description": "Цена снижена, срочно продаю"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "accepted"
    # Цена в патче — JSON-безопасная строка (Decimal → str)
    assert patched.json()["pending_patch"]["price"] == "35000"

    # Лента/PDP показывают СТАРУЮ цену до одобрения
    ids = await _feed_ids(client)
    assert listing_id in ids
    pdp = await _pdp(client, listing_id)
    assert pdp.status_code == 200
    assert pdp.json()["price"] == 38000.0

    lot = await session.get(Listing, listing_id)
    assert lot.status == "accepted"  # статус не меняется
    assert lot.price == Decimal("38000")  # поля не применены
    assert lot.pending_patch["price"] == "35000"
    assert lot.pending_patch["description"] == "Цена снижена, срочно продаю"

    # my-lots показывает патч на проверке; очередь patch у админа
    mine = _lot_in(await _my_lots(client, headers), listing_id)
    assert mine["pending_patch"]["price"] == "35000"
    queue = await client.get(
        "/api/admin/moderation",
        params={"queue": "patch"},
        headers={"X-Internal-Token": internal_token},
    )
    assert queue.status_code == 200
    assert listing_id in {item["listing_id"] for item in queue.json()}


async def test_patch_accept_applies_fields(
    client: AsyncClient,
    session: AsyncSession,
    internal_token: str,
    centrifugo_spy: _PublishSpy,
):
    """patch_accept применяет патч к полям (photos целиком), pending
    очищается, лента показывает новые данные + событие (10 §1)."""
    seller, headers = await _make_seller(session, 901109, "lc_seller_apply")
    listing_id = await _published_lot(client, session, seller, headers)

    patched = await client.patch(
        f"/api/market/lots/{listing_id}",
        json={
            "price": 35000,
            "photos": ["https://cdn.example.test/new.jpg"],
        },
        headers=headers,
    )
    assert patched.status_code == 200

    accepted = await client.post(
        f"/api/admin/moderation/{listing_id}/patch_accept",
        headers={"X-Internal-Token": internal_token},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    lot = await session.get(Listing, listing_id)
    assert lot.price == Decimal("35000")
    assert lot.images == ["https://cdn.example.test/new.jpg"]
    assert lot.pending_patch is None

    pdp = await _pdp(client, listing_id)
    assert pdp.json()["price"] == 35000.0

    events = _events(centrifugo_spy, f"user:{seller.telegram_id}", listing_id)
    patch_events = [e for e in events if e.get("patch") == "accepted"]
    assert len(patch_events) == 1
    assert patch_events[0]["status"] == "accepted"


async def test_patch_reject_keeps_reason(
    client: AsyncClient,
    session: AsyncSession,
    internal_token: str,
    centrifugo_spy: _PublishSpy,
):
    """patch_reject: патч остаётся с patch_reject_reason, поля не
    применяются, my-lots показывает причину (10 §1)."""
    seller, headers = await _make_seller(session, 901110, "lc_seller_rej")
    listing_id = await _published_lot(client, session, seller, headers)

    patched = await client.patch(
        f"/api/market/lots/{listing_id}",
        json={"price": 30000},
        headers=headers,
    )
    assert patched.status_code == 200

    rejected = await client.post(
        f"/api/admin/moderation/{listing_id}/patch_reject",
        json={"reason": "цена подозрительно низкая"},
        headers={"X-Internal-Token": internal_token},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["reason"] == "цена подозрительно низкая"

    lot = await session.get(Listing, listing_id)
    assert lot.price == Decimal("38000")  # патч НЕ применён
    assert lot.pending_patch["price"] == "30000"
    assert lot.pending_patch["patch_reject_reason"] == (
        "цена подозрительно низкая"
    )

    mine = _lot_in(await _my_lots(client, headers), listing_id)
    assert mine["pending_patch"]["patch_reject_reason"] == (
        "цена подозрительно низкая"
    )

    events = _events(centrifugo_spy, f"user:{seller.telegram_id}", listing_id)
    patch_events = [e for e in events if e.get("patch") == "rejected"]
    assert len(patch_events) == 1
    assert patch_events[0]["reason"] == "цена подозрительно низкая"


# =========================================================================
# (10) Лента рынка показывает только accepted
# =========================================================================


async def test_market_feed_hides_non_accepted_statuses(
    client: AsyncClient, session: AsyncSession
):
    """draft/moderation/rejected/expired/archived не попадают в чипс
    source=market — только accepted (10 §1)."""
    seller, _headers = await _make_seller(session, 901111, "lc_seller_feed")
    now = datetime.now(UTC)
    created: dict[str, Listing] = {}
    for key, status in (
        ("draft", "draft"),
        ("moderation", "moderation"),
        ("rejected", "rejected"),
        ("expired", "expired"),
        ("accepted", "accepted"),
    ):
        lot = Listing(
            source="market",
            source_id=f"lc-feed-{key}",
            url=f"/listing/market/lc-feed-{key}",
            make="Kia",
            model="Sorento",
            year=2022,
            price=Decimal("25000"),
            currency="USD",
            owner_type="user",
            owner_user_id=seller.telegram_id,
            is_active=True,
            status=status,
            parsed_at=now,
        )
        session.add(lot)
        created[key] = lot
    await session.flush()

    ids = await _feed_ids(client)
    assert created["accepted"].id in ids
    for key in ("draft", "moderation", "rejected", "expired"):
        assert created[key].id not in ids

    # my-lots без фильтра статуса: видны все
    # (проверяем через отдельного владельца-юзера my-lots)


# =========================================================================
# (11) Снятие лота: archived + событие
# =========================================================================


async def test_close_lot_archives_and_publishes(
    client: AsyncClient, session: AsyncSession, centrifugo_spy: _PublishSpy
):
    """DELETE accepted-лота → status='archived', is_active=false (совм.),
    из ленты исчезает, событие archived в личный канал (10 §1, §3)."""
    seller, headers = await _make_seller(session, 901112, "lc_seller_close")
    listing_id = await _published_lot(client, session, seller, headers)
    assert listing_id in await _feed_ids(client)

    closed = await client.delete(
        f"/api/market/lots/{listing_id}", headers=headers
    )
    assert closed.status_code == 200

    lot = await session.get(Listing, listing_id)
    assert lot.status == "archived"
    assert lot.is_active is False
    assert listing_id not in await _feed_ids(client)

    mine = _lot_in(await _my_lots(client, headers), listing_id)
    assert mine["status"] == "archived"

    events = _events(centrifugo_spy, f"user:{seller.telegram_id}", listing_id)
    assert [e["status"] for e in events][-1] == "archived"
