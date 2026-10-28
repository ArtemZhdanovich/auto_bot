"""Партнёрские чеки осмотра и история машины (07 §3).

PUT/GET /api/partner/listings/{id}/inspection по X-API-Key на своих
лотах (чужой → 404); тарифный гейт юзерского GET остаётся (Free → 403),
владелец-партнёр читает без гейта; история PUT/GET + ListingOut.owner_history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Listing
from app.infrastructure.security.tokens import create_access_token
from app.config import get_settings
from tests.partners.helpers import create_partner

pytestmark = pytest.mark.asyncio

INSPECTION_BODY = {
    "inspector": "Осмотрщик салона",
    "grade": "good",
    "summary": "Состояние соответствует пробегу",
    "marks": [
        {"zone": "hood", "type": "scratch", "note": "полировка"},
        {"zone": "door_fl", "type": "dent"},
    ],
    "items": [
        {"key": "engine", "status": "ok"},
        {"key": "brakes", "status": "warn", "note": "колодки к замене"},
    ],
    "images": ["http://localhost:9000/market-photos/photos/1/x.jpg"],
}


async def _partner(session: AsyncSession, name: str):
    """Партнёр с ключом: (account, raw_key)."""
    return await create_partner(
        session,
        name=name,
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100"),
        cpl_rate=None,
    )


async def _partner_lot(
    session: AsyncSession, partner_id: int, source_id: str
) -> Listing:
    """Активный partner-лот (владелец = счёту)."""
    lot = Listing(
        source="market",
        source_id=source_id,
        url=f"/listing/market/{source_id}",
        make="Genesis",
        model="GV80",
        year=2023,
        price=65000,
        currency="USD",
        owner_type="partner",
        owner_partner_id=partner_id,
        is_active=True,
        parsed_at=datetime.now(UTC),
    )
    session.add(lot)
    await session.flush()
    return lot


def _user_headers(telegram_id: int) -> dict:
    """Bearer-заголовок юзера (free-гейт проверки)."""
    return {
        "Authorization": f"Bearer {create_access_token(telegram_id, get_settings())}"
    }


# =========================================================================
# Чек: PUT свой/чужой, GET партнёром и юзерами
# =========================================================================


async def test_partner_inspection_put_own_and_get(
    client, session: AsyncSession, auth_headers: dict
):
    """PUT своего лота — 200 c source='partner'; GET партнёром — тот же чек."""
    account, raw_key = await _partner(session, "Чеки и история")
    lot = await _partner_lot(session, account.id, "insp-own-1")
    headers = {"X-API-Key": raw_key}

    put = await client.put(
        f"/api/partner/listings/{lot.id}/inspection",
        json=INSPECTION_BODY,
        headers=headers,
    )
    assert put.status_code == 200, put.text
    report = put.json()
    assert report["source"] == "partner"  # форсирован телом нельзя
    assert report["grade"] == "good"
    assert len(report["marks"]) == 2
    assert report["items"][1]["status"] == "warn"

    got = await client.get(
        f"/api/partner/listings/{lot.id}/inspection", headers=headers
    )
    assert got.status_code == 200, got.text
    assert got.json() == report


async def test_partner_inspection_foreign_lot_404(
    client, session: AsyncSession
):
    """Чужой лот и несуществующий — 404 (неотличимы) на PUT и GET."""
    owner, _owner_raw = await _partner(session, "Владелец")
    stranger, stranger_raw = await _partner(session, "Чужой салон")
    lot = await _partner_lot(session, owner.id, "insp-foreign-1")
    headers = {"X-API-Key": stranger_raw}

    put = await client.put(
        f"/api/partner/listings/{lot.id}/inspection",
        json=INSPECTION_BODY,
        headers=headers,
    )
    assert put.status_code == 404
    assert "lot_not_found" in put.json()["detail"]

    got = await client.get(
        f"/api/partner/listings/{lot.id}/inspection", headers=headers
    )
    assert got.status_code == 404

    assert (
        await client.put(
            "/api/partner/listings/999999999/inspection",
            json=INSPECTION_BODY,
            headers=headers,
        )
    ).status_code == 404


async def test_partner_inspection_schema_reused(
    client, session: AsyncSession
):
    """Тело = admin InspectionReportIn: невалидная зона — 422 (reuse схемы)."""
    account, raw_key = await _partner(session, "Схемщик")
    lot = await _partner_lot(session, account.id, "insp-schema-1")
    bad = {**INSPECTION_BODY, "marks": [{"zone": "warp_drive", "type": "dent"}]}
    resp = await client.put(
        f"/api/partner/listings/{lot.id}/inspection",
        json=bad,
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 422


async def test_user_tier_gate_still_applies(
    client,
    session: AsyncSession,
    free_user,
    auth_headers_admin: dict,
):
    """Юзерский GET: Free — 403 inspection_locked; Pro/Business — 200.

    Партнёрский PUT при этом доступен без тарифа, а владелец-партнёр
    читает свой чек через кабинет (bypass_tier, 07 §3).
    """
    account, raw_key = await _partner(session, "Гейтмейстер")
    lot = await _partner_lot(session, account.id, "insp-gate-1")
    assert (
        await client.put(
            f"/api/partner/listings/{lot.id}/inspection",
            json=INSPECTION_BODY,
            headers={"X-API-Key": raw_key},
        )
    ).status_code == 200

    # Free-юзер: чеки платные — 403 (прежнее поведение listings.py).
    locked = await client.get(
        f"/api/listings/{lot.id}/inspection",
        headers=_user_headers(free_user.telegram_id),
    )
    assert locked.status_code == 403
    assert "inspection_locked" in locked.json()["detail"]

    # Админ — как и раньше, без гейта.
    admin_view = await client.get(
        f"/api/listings/{lot.id}/inspection", headers=auth_headers_admin
    )
    assert admin_view.status_code == 200
    assert admin_view.json()["source"] == "partner"

    # Владелец-партнёр читает чек кабинетом, невзирая на тарифы юзеров.
    partner_view = await client.get(
        f"/api/partner/listings/{lot.id}/inspection",
        headers={"X-API-Key": raw_key},
    )
    assert partner_view.status_code == 200


# =========================================================================
# История машины: PUT сортировка, GET через ListingOut.owner_history
# =========================================================================


async def test_history_put_sorts_and_listing_out_returns(
    client, session: AsyncSession, auth_headers: dict
):
    """PUT сохраняет события по дате убыванием; GET /api/listings/{id}
    отдаёт owner_history всем (PDP-таймлайн)."""
    account, raw_key = await _partner(session, "Историк")
    lot = await _partner_lot(session, account.id, "hist-1")
    headers = {"X-API-Key": raw_key}

    resp = await client.put(
        f"/api/partner/listings/{lot.id}/history",
        json={
            "events": [
                {"date": "2021-06-10", "title": "Первая регистрация в Корее"},
                {"date": "2024-03-01", "title": "Таможня и постановка на учёт"},
                {"date": "2022-11-15", "title": "Замена ремня ГРМ", "note": "сервисный центр"},
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": True}

    pdp = (
        await client.get(f"/api/listings/{lot.id}", headers=auth_headers)
    ).json()
    history = pdp["owner_history"]
    # Свежие события первыми (сортировка при сохранении, 07 §3).
    assert [event["date"] for event in history] == [
        "2024-03-01",
        "2022-11-15",
        "2021-06-10",
    ]
    assert history[1]["note"] == "сервисный центр"
    assert history[2]["note"] is None

    # Повторный PUT заменяет историю целиком.
    replaced = await client.put(
        f"/api/partner/listings/{lot.id}/history",
        json={"events": [{"date": "2025-01-01", "title": "Новая история"}]},
        headers=headers,
    )
    assert replaced.status_code == 200
    pdp = (
        await client.get(f"/api/listings/{lot.id}", headers=auth_headers)
    ).json()
    assert pdp["owner_history"] == [
        {"date": "2025-01-01", "title": "Новая история", "note": None}
    ]


async def test_history_limits_and_ownership(
    client, session: AsyncSession
):
    """Больше 50 событий — 422; чужой лот — 404; внешний лот без владельца
    партнёру не принадлежит — 404."""
    owner, _owner_raw = await _partner(session, "Хозяин истории")
    stranger, stranger_raw = await _partner(session, "Случайный салон")
    lot = await _partner_lot(session, owner.id, "hist-2")
    headers = {"X-API-Key": stranger_raw}

    too_many = {
        "events": [
            {"date": "2020-01-01", "title": f"Событие {i}"} for i in range(51)
        ]
    }
    assert (
        await client.put(
            f"/api/partner/listings/{lot.id}/history",
            json=too_many,
            headers={"X-API-Key": _owner_raw},
        )
    ).status_code == 422

    foreign = await client.put(
        f"/api/partner/listings/{lot.id}/history",
        json={"events": [{"date": "2024-01-01", "title": "Чужая"}]},
        headers=headers,
    )
    assert foreign.status_code == 404
    assert "lot_not_found" in foreign.json()["detail"]

    # Внешний лот (encar, owner_type IS NULL) партнёру не принадлежит.
    external = Listing(
        source="encar",
        source_id="hist-external-1",
        url="https://example.test/encar/hist-external-1",
        parsed_at=datetime.now(UTC),
    )
    session.add(external)
    await session.flush()
    assert (
        await client.put(
            f"/api/partner/listings/{external.id}/history",
            json={"events": []},
            headers=headers,
        )
    ).status_code == 404


async def test_history_empty_by_default(
    client, session: AsyncSession, auth_headers: dict, listing
):
    """Без записей ListingOut.owner_history = [] (JSONB default)."""
    pdp = (
        await client.get(f"/api/listings/{listing.id}", headers=auth_headers)
    ).json()
    assert pdp["owner_history"] == []
