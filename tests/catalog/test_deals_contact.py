"""POST /api/listings/{id}/contact: заявка «свяжитесь со мной» (CPL-пул).

Механика монетизации: лид `source=web` уходит в пул партнёров. Проверяем
доступность для любого тарифа, запрет гостям (после входа), rate-гейт
«1 заявка на объявление в сутки» (sent_log kind='contact') и 401 без токена.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Lead, SentLog, User


@pytest.mark.asyncio
async def test_contact_creates_web_lead(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    listing,
):
    """201: создаётся лид source=web с комментарием по умолчанию."""
    resp = await client.post(
        f"/api/listings/{listing.id}/contact", json={}, headers=auth_headers
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["lead_id"] > 0
    assert body["status"] == "new"

    lead = (
        (
            await session.execute(
                select(Lead).where(
                    Lead.user_id == user.telegram_id,
                    Lead.listing_id == listing.id,
                )
            )
        )
        .scalars()
        .one()
    )
    assert lead.source == "web"
    assert lead.comment == "Интерес к автомобилю: свяжитесь со мной"
    # гейт зафиксировал заявку в sent_log
    sent = (
        await session.execute(
            select(SentLog).where(
                SentLog.user_id == user.telegram_id,
                SentLog.listing_id == listing.id,
                SentLog.kind == "contact",
            )
        )
    ).scalar_one_or_none()
    assert sent is not None


@pytest.mark.asyncio
async def test_contact_appends_user_comment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    listing,
):
    """Пользовательский комментарий добавляется к формулировке по умолчанию."""
    resp = await client.post(
        f"/api/listings/{listing.id}/contact",
        json={"comment": "Хочу посмотреть завтра"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    lead = (
        (
            await session.execute(
                select(Lead).where(
                    Lead.user_id == user.telegram_id,
                    Lead.listing_id == listing.id,
                )
            )
        )
        .scalars()
        .one()
    )
    assert lead.comment == (
        "Интерес к автомобилю: свяжитесь со мной. Хочу посмотреть завтра"
    )


@pytest.mark.asyncio
async def test_contact_guest_forbidden(
    client: AsyncClient, guest_headers: dict[str, str], listing
):
    """Гостевой аккаунт — 403 «после входа»."""
    resp = await client.post(
        f"/api/listings/{listing.id}/contact",
        json={},
        headers=guest_headers,
    )
    assert resp.status_code == 403
    assert "guest_forbidden" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_contact_requires_auth(client: AsyncClient, listing):
    """Без Bearer-токена — 401."""
    resp = await client.post(f"/api/listings/{listing.id}/contact", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_contact_duplicate_within_24h_conflict(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: User,
    listing,
):
    """Повторная заявка по тому же объявлению в окне суток — 409."""
    first = await client.post(
        f"/api/listings/{listing.id}/contact", json={}, headers=auth_headers
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/listings/{listing.id}/contact", json={}, headers=auth_headers
    )
    assert second.status_code == 409
    assert second.json()["detail"] == (
        "duplicate_contact: заявка по этому автомобилю уже отправлена сегодня"
    )


@pytest.mark.asyncio
async def test_contact_allowed_after_window(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    listing,
):
    """sent_log старше 24 часов не блокирует: создаётся новый лид."""
    session.add(
        SentLog(
            user_id=user.telegram_id,
            listing_id=listing.id,
            kind="contact",
            sent_at=datetime.now(UTC) - timedelta(hours=25),
        )
    )
    await session.flush()

    resp = await client.post(
        f"/api/listings/{listing.id}/contact", json={}, headers=auth_headers
    )
    assert resp.status_code == 201
    # окно сдвинулось: свежий sent_at
    sent_at = (
        await session.execute(
            select(SentLog.sent_at).where(
                SentLog.user_id == user.telegram_id,
                SentLog.listing_id == listing.id,
                SentLog.kind == "contact",
            )
        )
    ).scalar_one()
    assert datetime.now(UTC) - sent_at.replace(tzinfo=UTC) < timedelta(hours=1)


@pytest.mark.asyncio
async def test_contact_missing_listing_404(
    client: AsyncClient, auth_headers: dict[str, str]
):
    """Объявления нет — 404 (валидация CreateLead)."""
    resp = await client.post(
        "/api/listings/999999999/contact", json={}, headers=auth_headers
    )
    assert resp.status_code == 404
