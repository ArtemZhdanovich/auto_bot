"""Keyset-пагинация ленты /api/listings (PLAN.md, фаза 9, P2/P3).

Курсор кодирует (parsed_at, id) последнего элемента страницы; запрос идёт
`WHERE (parsed_at, id) < (:ts, :id) ORDER BY parsed_at DESC, id DESC`
без offset и без count(*). Без `cursor` поведение (offset + count) прежнее.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Listing

BASE_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def _make_listing(
    session: AsyncSession,
    *,
    source_id: str,
    parsed_at: datetime,
) -> Listing:
    """Создаёт активное объявление с заданным `parsed_at` и коммитит."""
    listing = Listing(
        source="encar",
        source_id=source_id,
        make="Keyset",
        model="Feed",
        year=2020,
        price=Decimal("20000"),
        currency="USD",
        mileage_km=50000,
        body_type="sedan",
        fuel="petrol",
        transmission="automatic",
        drive="fwd",
        color="white",
        engine_volume=Decimal("2.0"),
        url=f"https://www.encar.com/a/{source_id}/",
        is_active=True,
        parsed_at=parsed_at,
    )
    session.add(listing)
    await session.commit()
    await session.refresh(listing)
    return listing


async def _make_feed(session: AsyncSession, count: int = 5) -> list[int]:
    """Пять объявлений с убывающим `parsed_at`; возвращает id в порядке ленты."""
    created = [
        await _make_listing(
            session,
            source_id=f"ks-{i}",
            parsed_at=BASE_TS - timedelta(minutes=i),
        )
        for i in range(count)
    ]
    return [x.id for x in created]


@pytest.mark.asyncio
async def test_keyset_first_page_has_next_cursor(
    client: AsyncClient, session: AsyncSession,
):
    """?limit=2 → 2 элемента, next_cursor и has_more=True."""
    expected = await _make_feed(session)

    r = await client.get("/api/listings", params={"limit": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5
    assert [i["id"] for i in body["items"]] == expected[:2]
    assert body["has_more"] is True
    assert body["next_cursor"]


@pytest.mark.asyncio
async def test_keyset_pages_have_no_overlap(
    client: AsyncClient, session: AsyncSession,
):
    """Курсор ведёт по страницам без пропусков и повторов до конца ленты."""
    expected = await _make_feed(session)

    seen: list[int] = []
    cursor: str | None = None
    for page_no in range(3):
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        r = await client.get("/api/listings", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if page_no < 2:
            assert body["has_more"] is True
            assert cursor
        else:
            assert body["has_more"] is False
            assert cursor is None

    assert seen == expected
    assert len(set(seen)) == 5


@pytest.mark.asyncio
async def test_keyset_tie_on_parsed_at_uses_id_tiebreaker(
    client: AsyncClient, session: AsyncSession,
):
    """Одинаковый parsed_at не теряет и не дублирует строки (id в курсоре)."""
    first = await _make_listing(session, source_id="ks-tie-a", parsed_at=BASE_TS)
    second = await _make_listing(session, source_id="ks-tie-b", parsed_at=BASE_TS)
    third = await _make_listing(
        session,
        source_id="ks-tie-c",
        parsed_at=BASE_TS - timedelta(seconds=1),
    )
    expected = [max(first.id, second.id), min(first.id, second.id), third.id]

    seen: list[int] = []
    cursor: str | None = None
    for _ in range(5):  # ограничение: защита от бесконечного цикла
        params: dict[str, object] = {"limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        body = (await client.get("/api/listings", params=params)).json()
        seen.extend(item["id"] for item in body["items"])
        if not body["has_more"]:
            break
        cursor = body["next_cursor"]
    assert seen == expected


@pytest.mark.asyncio
async def test_cursor_with_other_sort_returns_422(
    client: AsyncClient, session: AsyncSession,
):
    """Курсор определён только для sort=new; price_asc + cursor → 422."""
    await _make_feed(session, count=3)
    first = (await client.get("/api/listings", params={"limit": 1})).json()
    cursor = first["next_cursor"]
    assert cursor

    r = await client.get(
        "/api/listings",
        params={"limit": 1, "cursor": cursor, "sort": "price_asc"},
    )
    assert r.status_code == 422
    assert "sort=new" in r.json()["detail"]


@pytest.mark.asyncio
async def test_non_new_sort_returns_no_cursor(
    client: AsyncClient, session: AsyncSession,
):
    """Курсор определён только для sort=new — у price_asc его нет."""
    await _make_feed(session, count=3)

    body = (
        await client.get(
            "/api/listings", params={"limit": 1, "sort": "price_asc"},
        )
    ).json()
    assert body["total"] == 3
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_invalid_cursor_returns_422(client: AsyncClient):
    """Мусор в cursor — 422 с понятным detail, а не 500."""
    r = await client.get("/api/listings", params={"cursor": "not-a-cursor"})
    assert r.status_code == 422
    assert "Invalid cursor" in r.json()["detail"]


@pytest.mark.asyncio
async def test_cursor_page_runs_single_query(
    client: AsyncClient, session: AsyncSession, query_counter: dict[str, int],
):
    """Курсорная страница — один SELECT: count(*) не выполняется (P3)."""
    await _make_feed(session)
    first = (await client.get("/api/listings", params={"limit": 2})).json()
    cursor = first["next_cursor"]
    assert cursor

    before = query_counter["n"]
    r = await client.get(
        "/api/listings", params={"limit": 2, "cursor": cursor},
    )
    assert r.status_code == 200, r.text
    assert query_counter["n"] - before == 1


@pytest.mark.asyncio
async def test_without_cursor_offset_and_total_unchanged(
    client: AsyncClient, session: AsyncSession,
):
    """Без cursor — offset + count: total верный, next_cursor не отдаётся."""
    await _make_feed(session)

    first = (await client.get("/api/listings", params={"limit": 2})).json()
    assert first["total"] == 5
    assert len(first["items"]) == 2
    # total посчитан (offset-режим), курсор — для следующей страницы.
    assert first["has_more"] is True
    assert first["next_cursor"]

    tail = (
        await client.get("/api/listings", params={"limit": 2, "offset": 4})
    ).json()
    assert tail["total"] == 5
    assert len(tail["items"]) == 1
    assert tail["next_cursor"] is None
    assert tail["has_more"] is False
