"""Похожие машины: FindSimilar (LLM-реранк/фолбэк), similar_candidates, API.

Юнит-тесты сценария — на фейках (без БД); SQL и HTTP — на тестовой БД
(фикстуры `session`/`client` из conftest).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.analysis.similar import (
    CANDIDATES_LIMIT,
    FALLBACK_REASON,
    FindSimilar,
)
from app.application.catalog.dto import ListingCard
from app.application.errors import NotFound
from app.infrastructure.db.models import Listing, PriceAnalysis, User
from app.infrastructure.db.repositories.listings_read import (
    SqlAlchemyListingReadRepository,
)

_BASE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _card(
    card_id: int,
    *,
    make: str = "Kia",
    model: str = "Carnival",
    year: int | None = 2021,
    price: str | None = "30000",
    mileage_km: int | None = 60000,
    images: tuple[str, ...] = ("https://img.test/1.jpg",),
) -> ListingCard:
    """Карточка для юнит-тестов FindSimilar."""
    return ListingCard(
        id=card_id,
        source="encar",
        source_id=f"s-{card_id}",
        url=f"https://encar.com/car/{card_id}",
        parsed_at=_BASE,
        make=make,
        model=model,
        year=year,
        price=Decimal(price) if price is not None else None,
        currency="USD",
        mileage_km=mileage_km,
        images=images,
    )


class FakeListingsRepository:
    """`cards_by_ids`: ровно один целевой листинг (или ничего)."""

    def __init__(self, card: ListingCard | None) -> None:
        self._card = card

    async def cards_by_ids(self, listing_ids: list[int]) -> dict:
        return {self._card.id: self._card} if self._card is not None else {}


class FakeReadRepository:
    """`similar_candidates`: предзаданный список карточек."""

    def __init__(self, candidates: list[ListingCard]) -> None:
        self._candidates = candidates
        self.last_limit: int | None = None

    async def similar_candidates(
        self, listing_id: int, *, limit: int = 24
    ) -> list[ListingCard]:
        self.last_limit = limit
        return self._candidates


class FakeLLM:
    """LLM-заглушка: фикс-ответ или ошибка; помнит последний промпт."""

    def __init__(
        self, text: str | None = None, error: Exception | None = None
    ) -> None:
        self._text = text
        self._error = error
        self.messages: list[dict] | None = None

    async def complete(self, messages: list[dict]) -> object:
        if self._error is not None:
            raise self._error
        self.messages = messages
        return SimpleNamespace(text=self._text, tool_calls=[])


def _use_case(
    target: ListingCard | None,
    candidates: list[ListingCard],
    llm: FakeLLM | None = None,
) -> tuple[FindSimilar, FakeReadRepository]:
    """Собрать FindSimilar на фейках (+ фабрика LLM, если передана)."""
    read = FakeReadRepository(candidates)
    factory = (lambda: llm) if llm is not None else None
    return FindSimilar(FakeListingsRepository(target), read, factory), read


# ---------- юнит: реранк ----------


@pytest.mark.asyncio
async def test_rerank_takes_cards_by_id_with_reasons():
    """LLM вернул JSON: карточки из кандидатов, reason/score из ответа."""
    target = _card(1, price="30000")
    candidates = [_card(i, price=f"{30000 + i * 100}") for i in range(2, 8)]
    llm = FakeLLM(
        text=(
            '```json\n{"items": ['
            '{"id": 3, "reason": "Тот же год и пробег", "score": 0.9},'
            '{"id": 999, "reason": "нет такой", "score": 1.0},'
            '{"id": 5, "reason": "Похожая комплектация", "score": 0.7}'
            "]}\n```"
        )
    )
    use_case, read = _use_case(target, candidates, llm)

    result = await use_case.execute(1)

    assert result.source == "ai"
    assert [item.card.id for item in result.items] == [3, 5]  # 999 пропущен
    assert result.items[0].reason == "Тот же год и пробег"
    assert result.items[0].score == 0.9
    assert read.last_limit == CANDIDATES_LIMIT
    # Промпт: system с инструкцией, user — целевая карточка + кандидаты.
    assert llm.messages is not None
    system, user = llm.messages[0], llm.messages[1]
    assert system["role"] == "system" and "автоэксперт" in system["content"]
    assert '"make": "Kia"' in user["content"]
    assert '"id": 3' in user["content"]


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_price():
    """Сбой LLM — warning и фолбэк по цене, source=stats."""
    target = _card(1, price="30000")
    candidates = [_card(i, price=f"{30000 + i * 100}") for i in range(2, 8)]
    use_case, _ = _use_case(target, candidates, FakeLLM(error=RuntimeError("boom")))

    result = await use_case.execute(1)

    assert result.source == "stats"
    assert [item.card.id for item in result.items] == [2, 3, 4, 5, 6]
    assert all(item.reason == FALLBACK_REASON for item in result.items)


@pytest.mark.asyncio
async def test_llm_bad_json_falls_back():
    """Мусор вместо JSON — фолбэк, не 500."""
    target = _card(1, price="30000")
    candidates = [_card(i, price=f"{30000 + i * 100}") for i in range(2, 8)]
    use_case, _ = _use_case(target, candidates, FakeLLM(text="не JSON вообще"))

    result = await use_case.execute(1)

    assert result.source == "stats"
    assert len(result.items) == 5


@pytest.mark.asyncio
async def test_few_candidates_skip_llm():
    """Кандидатов не больше limit — LLM не вызывается (реранчить нечего)."""
    target = _card(1, price="30000")
    candidates = [_card(2, price="29000"), _card(3, price="32000")]
    llm = FakeLLM(text='{"items": [{"id": 2, "reason": "x", "score": 1}]}')
    use_case, _ = _use_case(target, candidates, llm)

    result = await use_case.execute(1)

    assert result.source == "stats"
    assert [item.card.id for item in result.items] == [2, 3]
    assert llm.messages is None  # LLM не звали


@pytest.mark.asyncio
async def test_no_llm_factory_price_fallback():
    """Фабрики нет вовсе — работает фолбэк по цене."""
    target = _card(1, price="30000")
    candidates = [_card(2, price="32000"), _card(3, price="29000")]
    use_case, _ = _use_case(target, candidates, llm=None)

    result = await use_case.execute(1)

    assert result.source == "stats"
    assert [item.card.id for item in result.items] == [3, 2]


@pytest.mark.asyncio
async def test_missing_listing_not_found():
    """Целевого объявления нет — NotFound (404 на транспорте)."""
    use_case, read = _use_case(None, [_card(2)])

    with pytest.raises(NotFound):
        await use_case.execute(1)
    assert read.last_limit is None  # кандидатов даже не спрашивали


@pytest.mark.asyncio
async def test_no_candidates_empty_result():
    """Кандидатов нет — пустой результат без LLM."""
    llm = FakeLLM(text='{"items": [{"id": 2, "reason": "x", "score": 1}]}')
    use_case, _ = _use_case(_card(1), [], llm)

    result = await use_case.execute(1)

    assert result.items == []
    assert result.source == "stats"
    assert llm.messages is None


# ---------- SQL: similar_candidates ----------


async def _make_listing(
    session: AsyncSession,
    source_id: str,
    *,
    make: str | None = "Kia",
    model: str | None = "Carnival",
    year: int | None = 2021,
    price: Decimal | None = Decimal("20000"),
    is_active: bool = True,
) -> Listing:
    listing = Listing(
        source="encar",
        source_id=source_id,
        url=f"https://encar.com/a/{source_id}/",
        make=make,
        model=model,
        year=year,
        price=price,
        currency="USD",
        images=[f"https://img.test/{source_id}.jpg"],
        parsed_at=_BASE,
        is_active=is_active,
    )
    session.add(listing)
    await session.flush()
    return listing


@pytest.mark.asyncio
async def test_similar_candidates_filters_and_order(session: AsyncSession):
    """Та же марка/модель (case-insensitive), ±3 года, цена ±35%, без себя."""
    target = await _make_listing(session, "target")
    close = await _make_listing(session, "close", price=Decimal("21000"))
    no_year = await _make_listing(session, "no-year", year=None, price=Decimal("20000"))
    cheaper = await _make_listing(session, "cheaper", price=Decimal("19000"))
    edge = await _make_listing(session, "edge", price=Decimal("26500"))  # +32.5%
    lower_make = await _make_listing(
        session, "lower", make="kia", model="carnival", price=Decimal("22000")
    )
    await _make_listing(session, "too-pricey", price=Decimal("28000"))  # +40%
    await _make_listing(session, "other-model", model="K5")
    await _make_listing(session, "old-year", year=2017)
    await _make_listing(session, "no-price", price=None)
    await _make_listing(session, "inactive", is_active=False)

    repo = SqlAlchemyListingReadRepository(session)
    cards = await repo.similar_candidates(target.id, limit=24)

    # Сортировка по близости цены: 0 (no-year), 1000, 2000, 22000-?, 26500.
    assert [c.id for c in cards] == [
        no_year.id,
        cheaper.id,
        close.id,
        lower_make.id,
        edge.id,
    ]
    assert all(c.is_active for c in cards)
    assert target.id not in {c.id for c in cards}


@pytest.mark.asyncio
async def test_similar_candidates_limit(session: AsyncSession):
    """limit режет выборку кандидатов."""
    target = await _make_listing(session, "lim-target")
    for i in range(4):
        await _make_listing(session, f"lim-{i}", price=Decimal(f"{19000 + i}"))
    repo = SqlAlchemyListingReadRepository(session)
    cards = await repo.similar_candidates(target.id, limit=2)
    assert len(cards) == 2


@pytest.mark.asyncio
async def test_similar_candidates_target_without_price(session: AsyncSession):
    """У исходного нет цены: только марка/модель/год, цена кандидата любая."""
    target = await _make_listing(session, "np-target", price=None)
    pricey = await _make_listing(session, "np-pricey", price=Decimal("90000"))
    free = await _make_listing(session, "np-free", price=None)
    same_model = await _make_listing(session, "np-same", price=Decimal("5000"))

    repo = SqlAlchemyListingReadRepository(session)
    cards = await repo.similar_candidates(target.id)

    assert {c.id for c in cards} == {pricey.id, free.id, same_model.id}


@pytest.mark.asyncio
async def test_similar_candidates_unknown_listing(session: AsyncSession):
    """Исходного объявления нет — пустой список (404 решает сценарий)."""
    repo = SqlAlchemyListingReadRepository(session)
    assert await repo.similar_candidates(999999) == []


# ---------- API: POST /api/listings/{id}/similar ----------


class _RerankLLM:
    """LLM-заглушка для API-теста: берёт id первых кандидатов из промпта."""

    async def complete(self, messages: list[dict]) -> object:
        candidates = json.loads(messages[1]["content"].split("Кандидаты: ", 1)[1])
        top = candidates[:2]
        return SimpleNamespace(
            text=json.dumps(
                {
                    "items": [
                        {"id": c["id"], "reason": f"Тот же {c['year']} год", "score": 0.9}
                        for c in top
                    ]
                },
                ensure_ascii=False,
            ),
            tool_calls=[],
        )


@pytest.mark.asyncio
async def test_post_similar_stats_fallback(
    client: AsyncClient,
    auth_headers_user: dict[str, str],
    session: AsyncSession,
):
    """Кандидатов меньше limit — фолбэк по цене: 200, source=stats."""
    target = await _make_listing(session, "api-target")
    near = await _make_listing(session, "api-near", price=Decimal("21000"))
    await _make_listing(session, "api-far", model="K5")  # не кандидат
    await session.commit()

    resp = await client.post(f"/api/listings/{target.id}/similar", headers=auth_headers_user)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "stats"
    assert [item["id"] for item in body["items"]] == [near.id]
    item = body["items"][0]
    assert item["make"] == "Kia" and item["model"] == "Carnival"
    assert item["price"] == 21000.0
    assert item["url"] == near.url
    assert item["image"] == "https://img.test/api-near.jpg"
    assert item["reason"] == FALLBACK_REASON


@pytest.mark.asyncio
async def test_post_similar_ai_rerank(
    client: AsyncClient,
    auth_headers_user: dict[str, str],
    session: AsyncSession,
    monkeypatch,
):
    """Кандидатов больше limit и LLM жив — source=ai с reason из модели."""
    target = await _make_listing(session, "ai-target")
    for i in range(6):
        await _make_listing(session, f"ai-{i}", price=Decimal(f"{20000 + i * 100}"))
    await session.commit()
    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: _RerankLLM()
    )

    resp = await client.post(f"/api/listings/{target.id}/similar", headers=auth_headers_user)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "ai"
    assert len(body["items"]) == 2
    assert all(item["reason"].startswith("Тот же") for item in body["items"])
    assert all(item["score"] == 0.9 for item in body["items"])


@pytest.mark.asyncio
async def test_post_similar_missing_listing_404(
    client: AsyncClient, auth_headers_user: dict[str, str]
):
    resp = await client.post("/api/listings/999999/similar", headers=auth_headers_user)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_similar_requires_auth(client: AsyncClient):
    resp = await client.post("/api/listings/1/similar")
    assert resp.status_code == 401


# ---------- API: GET /api/me/analyses ----------


async def _add_analysis(
    session: AsyncSession,
    *,
    listing_id: int,
    user_id: int,
    verdict: str = "fair",
    summary: str = "Оценка по статистике рынка.",
) -> PriceAnalysis:
    row = PriceAnalysis(
        listing_id=listing_id,
        user_id=user_id,
        verdict=verdict,
        fair_min=Decimal("19000"),
        fair_max=Decimal("21000"),
        summary=summary,
        payload={},
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_my_analyses_returns_own_history(
    client: AsyncClient,
    auth_headers_user: dict[str, str],
    session: AsyncSession,
    user: User,
    listing: Listing,
):
    """История разборов пользователя с карточкой машины, новые сверху."""
    first = await _add_analysis(
        session, listing_id=listing.id, user_id=user.telegram_id
    )
    other = Listing(
        source="encar",
        source_id="history-other",
        url="https://encar.com/a/history-other/",
        make="BMW",
        model="520i",
        year=2018,
        price=Decimal("17000"),
        currency="USD",
        images=["https://img.test/bmw.jpg"],
        parsed_at=_BASE,
    )
    session.add(other)
    await session.flush()
    second = await _add_analysis(
        session,
        listing_id=other.id,
        user_id=user.telegram_id,
        verdict="overpriced",
        summary="Дорого",
    )
    # Чужой разбор не виден; свой по листингу без... всё с карточками.
    await _add_analysis(
        session, listing_id=listing.id, user_id=user.telegram_id + 777
    )
    await session.commit()

    resp = await client.get("/api/me/analyses", headers=auth_headers_user)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [row["id"] for row in body] == [second.id, first.id]
    newest = body[0]
    assert newest["listing_id"] == other.id
    assert newest["verdict"] == "overpriced"
    assert newest["make"] == "BMW" and newest["model"] == "520i"
    assert newest["price"] == 17000.0
    assert newest["image"] == "https://img.test/bmw.jpg"
    assert newest["fair_min"] == 19000.0 and newest["fair_max"] == 21000.0
    oldest = body[1]
    assert oldest["summary"] == "Оценка по статистике рынка."
    assert oldest["make"] == "Kia"


@pytest.mark.asyncio
async def test_my_analyses_limit(
    client: AsyncClient,
    auth_headers_user: dict[str, str],
    session: AsyncSession,
    user: User,
    listing: Listing,
):
    """limit=1 отдаёт один разбор; limit=101 — 422."""
    await _add_analysis(session, listing_id=listing.id, user_id=user.telegram_id)
    other = await _make_listing(session, "lim-analyses")
    await _add_analysis(
        session, listing_id=other.id, user_id=user.telegram_id, verdict="deal"
    )
    await session.commit()

    resp = await client.get(
        "/api/me/analyses", params={"limit": 1}, headers=auth_headers_user
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    bad = await client.get(
        "/api/me/analyses", params={"limit": 101}, headers=auth_headers_user
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_my_analyses_empty_for_new_user(
    client: AsyncClient, auth_headers_user: dict[str, str]
):
    resp = await client.get("/api/me/analyses", headers=auth_headers_user)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_my_analyses_requires_auth(client: AsyncClient):
    resp = await client.get("/api/me/analyses")
    assert resp.status_code == 401
