"""Флаг `use_case_favorites`: toggle избранного через use case (фаза 3)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.infrastructure.db.models import Favorite, Listing


async def _make_listing(session, source_id: str) -> Listing:
    listing = Listing(
        source="test",
        source_id=source_id,
        url=f"https://example.com/{source_id}",
    )
    session.add(listing)
    await session.flush()
    return listing


@pytest.mark.asyncio
async def test_toggle_via_use_case_keeps_contract(
    client, session, user, auth_headers_user, monkeypatch
):
    """Флаг включён: тот же ответ, запись в БД, повторный toggle снимает."""
    monkeypatch.setattr(get_settings(), "use_case_favorites", True)
    listing = await _make_listing(session, "flag-1")

    first = await client.post(
        f"/api/listings/{listing.id}/favorite", headers=auth_headers_user
    )
    assert first.status_code == 200
    assert first.json() == {"favorited": True}

    rows = (
        await session.execute(
            select(Favorite).where(
                Favorite.user_id == user.telegram_id,
                Favorite.listing_id == listing.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1

    second = await client.post(
        f"/api/listings/{listing.id}/favorite", headers=auth_headers_user
    )
    assert second.status_code == 200
    assert second.json() == {"favorited": False}


@pytest.mark.asyncio
async def test_toggle_via_use_case_missing_listing_404(
    client, session, user, auth_headers_user, monkeypatch
):
    """Доменная ошибка маппится в 404, контракт сохранён."""
    monkeypatch.setattr(get_settings(), "use_case_favorites", True)

    response = await client.post(
        "/api/listings/999999999/favorite", headers=auth_headers_user
    )

    assert response.status_code == 404
