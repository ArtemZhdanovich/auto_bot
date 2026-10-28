"""Владелец партнёрского счёта и рынок (прод-ревизия 2026-10).

Две связанные проверки на живой тестовой БД:
- `owner_telegram_id`: создание партнёра выдаёт владельцу бессрочный
  Business (`grant_partner_business`), suspended возвращает на free,
  реактивация — снова Business (правило живёт в репозитории);
- рынок `/api/partner/market`: медиана/мин/макс по активным объявлениям
  с ценой и «выгодные» лоты дешевле 93% медианы (SQL-репозиторий).

Admin-ручки идут через ASGI-клиент (та же схема, что `test_partners`).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.application.errors import InvalidInput
from app.application.partners.queries import CreatePartner, UpdatePartner
from app.domain.subscription.policies import effective_tier
from app.infrastructure.db.models import Listing, PartnerAccount, User
from app.infrastructure.db.repositories.market import (
    SqlAlchemyPartnerMarketRepository,
)
from app.infrastructure.services.partners import SqlAlchemyPartnerRepository
from app.main_api import app
from conftest import FakeRedis, install_test_container
from tests.partners.helpers import create_partner

# --- Фикстуры и фабрики ------------------------------------------------------


@pytest.fixture
async def partner_client(session, fake_redis: FakeRedis):
    """ASGI-клиент: сессия теста + FakeRedis вместо настоящего Redis."""
    _prev = app.state.dishka_container
    _container = install_test_container(app, session=session, redis=fake_redis)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            yield http
    finally:
        app.state.dishka_container = _prev
        await _container.close()


async def make_user(session, telegram_id: int) -> User:
    user = User(
        telegram_id=telegram_id,
        username=f"owner_{telegram_id}",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


async def make_partner(session, **kw) -> tuple[PartnerAccount, str]:
    """Партнёр + первый ключ. По умолчанию: pro, квота 50, баланс 100."""
    params: dict = {
        "name": "Партнёр",
        "contact": None,
        "plan": "pro",
        "daily_quota": 50,
        "balance": Decimal("100.00"),
        "cpl_rate": None,
    }
    params.update(kw)
    return await create_partner(session, **params)


async def make_listing(
    session,
    *,
    price: int | Decimal,
    seq: int,
    make: str = "Kia",
    model: str = "Carnival",
    is_active: bool = True,
) -> Listing:
    listing = Listing(
        source="encar",
        source_id=f"mkt-{seq}",
        url=f"https://example.test/encar/mkt-{seq}",
        make=make,
        model=model,
        year=2021,
        price=Decimal(str(price)),
        currency="USD",
        mileage_km=60_000,
        parsed_at=datetime.now(UTC),
        is_active=is_active,
    )
    session.add(listing)
    await session.flush()
    return listing


# --- Владелец → Business -----------------------------------------------------


@pytest.mark.asyncio
async def test_create_partner_grants_owner_business(session):
    """Создание партнёра с владельцем: бесплатный пользователь получает
    бессрочный Business (until IS NULL), `effective_tier` это видит."""
    user = await make_user(session, 906_001)
    account, _ = await make_partner(session, owner_telegram_id=user.telegram_id)

    assert account.owner_telegram_id == user.telegram_id
    assert user.subscription_tier == "business"
    assert user.subscription_until is None
    assert effective_tier(user) == "business"


@pytest.mark.asyncio
async def test_create_partner_without_owner_keeps_user_tier(session):
    """Без владельца тарифы пользователей не трогаются."""
    user = await make_user(session, 906_002)
    account, _ = await make_partner(session)

    assert account.owner_telegram_id is None
    assert user.subscription_tier == "free"


@pytest.mark.asyncio
async def test_create_partner_unknown_owner_rejected(session):
    """Несуществующий владелец — InvalidInput, счёт не создаётся."""
    repo = SqlAlchemyPartnerRepository(session)
    with pytest.raises(InvalidInput):
        await CreatePartner(repo).execute(
            name="ИП Иванов",
            contact=None,
            plan="pro",
            daily_quota=10,
            balance=Decimal("100.00"),
            cpl_rate=None,
            owner_telegram_id=906_003,
        )

    accounts = (
        await session.execute(select(func.count()).select_from(PartnerAccount))
    ).scalar_one()
    assert accounts == 0


@pytest.mark.asyncio
async def test_suspend_returns_owner_to_free(session):
    """suspended → владелец возвращается на free (until IS NULL)."""
    user = await make_user(session, 906_010)
    account, _ = await make_partner(session, owner_telegram_id=user.telegram_id)
    repo = SqlAlchemyPartnerRepository(session)

    view = await UpdatePartner(repo).execute(account.id, status="suspended")

    assert view.status == "suspended"
    assert view.owner_telegram_id == user.telegram_id
    assert user.subscription_tier == "free"
    assert user.subscription_until is None
    assert effective_tier(user) == "free"


@pytest.mark.asyncio
async def test_reactivate_restores_business(session):
    """Реактивация suspended-партнёра возвращает владельцу Business."""
    user = await make_user(session, 906_011)
    account, _ = await make_partner(session, owner_telegram_id=user.telegram_id)
    repo = SqlAlchemyPartnerRepository(session)

    await UpdatePartner(repo).execute(account.id, status="suspended")
    assert user.subscription_tier == "free"

    await UpdatePartner(repo).execute(account.id, status="active")
    assert user.subscription_tier == "business"
    assert user.subscription_until is None


@pytest.mark.asyncio
async def test_quota_update_keeps_owner_business(session):
    """Правка квоты не трогает тариф владельца (синхронизация — только
    по владельцу/статусу)."""
    user = await make_user(session, 906_012)
    account, _ = await make_partner(session, owner_telegram_id=user.telegram_id)
    repo = SqlAlchemyPartnerRepository(session)

    await UpdatePartner(repo).execute(account.id, daily_quota=77)

    assert account.daily_quota == 77
    assert user.subscription_tier == "business"


@pytest.mark.asyncio
async def test_owner_change_grants_business_to_new_owner(session):
    """Смена владельца выдаёт Business новому; у прежнего тариф не
    снимается (снятие — только через suspended)."""
    first = await make_user(session, 906_020)
    second = await make_user(session, 906_021)
    account, _ = await make_partner(session, owner_telegram_id=first.telegram_id)
    repo = SqlAlchemyPartnerRepository(session)

    view = await UpdatePartner(repo).execute(
        account.id, owner_telegram_id=second.telegram_id
    )

    assert view.owner_telegram_id == second.telegram_id
    assert second.subscription_tier == "business"
    assert first.subscription_tier == "business"


@pytest.mark.asyncio
async def test_update_partner_unknown_owner_rejected(session):
    """Смена владельца на несуществующего пользователя — InvalidInput."""
    user = await make_user(session, 906_022)
    account, _ = await make_partner(session, owner_telegram_id=user.telegram_id)
    repo = SqlAlchemyPartnerRepository(session)

    with pytest.raises(InvalidInput):
        await UpdatePartner(repo).execute(
            account.id, owner_telegram_id=906_023
        )


# --- Admin API: владелец -----------------------------------------------------


@pytest.mark.asyncio
async def test_admin_create_partner_with_owner_grants_business(
    partner_client, session, internal_token: str
):
    """POST /api/admin/partners с owner_telegram_id: 201 + Business."""
    user = await make_user(session, 906_030)

    resp = await partner_client.post(
        "/api/admin/partners",
        json={
            "name": "ИП Иванов",
            "plan": "pro",
            "daily_quota": 50,
            "owner_telegram_id": user.telegram_id,
        },
        headers={"X-Internal-Token": internal_token},
    )

    assert resp.status_code == 201
    await session.refresh(user)
    assert user.subscription_tier == "business"
    assert user.subscription_until is None


@pytest.mark.asyncio
async def test_admin_create_partner_unknown_owner_422(
    partner_client, internal_token: str
):
    """Несуществующий владелец — 422, а не 500."""
    resp = await partner_client.post(
        "/api/admin/partners",
        json={"name": "ИП Иванов", "owner_telegram_id": 906_031},
        headers={"X-Internal-Token": internal_token},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_suspend_returns_owner_to_free(
    partner_client, session, internal_token: str
):
    """PATCH status=suspended: владелец на free, поле owner в ответе."""
    user = await make_user(session, 906_032)
    account, _ = await make_partner(session, owner_telegram_id=user.telegram_id)

    resp = await partner_client.patch(
        f"/api/admin/partners/{account.id}",
        json={"status": "suspended"},
        headers={"X-Internal-Token": internal_token},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "suspended"
    assert body["owner_telegram_id"] == user.telegram_id
    await session.refresh(user)
    assert user.subscription_tier == "free"
    assert user.subscription_until is None

    # список партнёров тоже отдаёт владельца (PartnerWithStatsOut)
    listing = await partner_client.get(
        "/api/admin/partners", headers={"X-Internal-Token": internal_token}
    )
    assert listing.status_code == 200
    assert any(
        item["owner_telegram_id"] == user.telegram_id
        for item in listing.json()
    )


@pytest.mark.asyncio
async def test_admin_update_partner_unknown_owner_422(
    partner_client, session, internal_token: str
):
    """PATCH с несуществующим владельцем — 422, а не 500."""
    account, _ = await make_partner(session)

    resp = await partner_client.patch(
        f"/api/admin/partners/{account.id}",
        json={"owner_telegram_id": 906_033},
        headers={"X-Internal-Token": internal_token},
    )

    assert resp.status_code == 422


# --- Рынок: репозиторий ------------------------------------------------------


@pytest.fixture
async def kia_market(session):
    """Каталог Kia Carnival: медиана 20000, выгодные 9000 и 15000.

    Шум: неактивный лот, нулевая цена и другая марка — в срез не попадают.
    """
    for seq, price in enumerate((9000, 15000, 20000, 20000, 25000, 40000)):
        await make_listing(session, price=price, seq=seq)
    await make_listing(session, price=100, seq=90, is_active=False)
    await make_listing(session, price=0, seq=91)
    await make_listing(session, price=50_000, seq=92, make="Hyundai", model="Sonata")
    return SqlAlchemyPartnerMarketRepository(session)


@pytest.mark.asyncio
async def test_market_report_median_and_discounts(kia_market):
    """Сводка по марке: count/min/max/медиана + deals дешевле 93% медианы,
    сортировка по выгоде (дешевле — выше)."""
    report = await kia_market.report(make="kia", deals_limit=10)

    summary = report.summary
    assert summary.make == "kia"
    assert summary.count == 6
    assert summary.min_price == 9000
    assert summary.max_price == 40000
    assert summary.median_price == 20000  # (20000 + 20000) / 2

    assert [deal["price"] for deal in report.deals] == [9000, 15000]
    assert report.deals[0]["discount_pct"] == 55.0  # 1 − 9000/20000
    assert report.deals[1]["discount_pct"] == 25.0  # 1 − 15000/20000
    first = report.deals[0]
    assert first["make"] == "Kia" and first["currency"] == "USD"
    assert first["url"].startswith("https://example.test/encar/mkt-")


@pytest.mark.asyncio
async def test_market_report_make_filter_case_insensitive(kia_market):
    """Фильтр по марке не зависит от регистра (эхо — как введено)."""
    for make in ("KIA", "kia", " Kia "):
        report = await kia_market.report(make=make)
        assert report.summary.make == make.strip()
        assert report.summary.count == 6


@pytest.mark.asyncio
async def test_market_report_whole_catalog_without_make(kia_market):
    """Без make — весь каталог: общая медиана, без группировки по моделям."""
    report = await kia_market.report()

    assert report.summary.make is None
    assert report.summary.count == 7  # Kia×6 + Hyundai
    assert report.summary.median_price == 20000
    # выгодные те же: Hyundai за 50000 дороже медианы
    assert [deal["price"] for deal in report.deals] == [9000, 15000]


@pytest.mark.asyncio
async def test_market_report_deals_limit(kia_market):
    """limit ограничивает выдачу выгодных лотов (самый выгодный первый)."""
    report = await kia_market.report(make="kia", deals_limit=1)

    assert [deal["price"] for deal in report.deals] == [9000]


@pytest.mark.asyncio
async def test_market_report_odd_median(session):
    """Нечётное число цен: медиана — средний элемент."""
    repo = SqlAlchemyPartnerMarketRepository(session)
    for seq, price in enumerate((10000, 20000, 29000)):
        await make_listing(session, price=price, seq=seq + 10)

    report = await repo.report(make="kia")

    assert report.summary.median_price == 20000
    assert [deal["price"] for deal in report.deals] == [10000]
    assert report.deals[0]["discount_pct"] == 50.0


@pytest.mark.asyncio
async def test_market_report_model_filter(session):
    """Фильтр по модели сужает срез (регистр не важен)."""
    repo = SqlAlchemyPartnerMarketRepository(session)
    await make_listing(session, price=10000, seq=20, model="Carnival")
    await make_listing(session, price=30000, seq=21, model="Sorento")

    report = await repo.report(make="kia", model="carnival")

    assert report.summary.model == "carnival"
    assert report.summary.count == 1
    assert report.summary.median_price == 10000


@pytest.mark.asyncio
async def test_market_report_empty_slice(session):
    """Пустой срез: нули и None без ошибок."""
    repo = SqlAlchemyPartnerMarketRepository(session)

    report = await repo.report(make="bmw")

    assert report.summary.count == 0
    assert report.summary.min_price is None
    assert report.summary.max_price is None
    assert report.summary.median_price is None
    assert report.deals == []


# --- Рынок: API /api/partner/market ------------------------------------------


@pytest.mark.asyncio
async def test_partner_market_endpoint(partner_client, session):
    """GET /api/partner/market по API-ключу: сводка + deals."""
    _, raw = await make_partner(session)
    for seq, price in enumerate((9000, 15000, 20000, 20000, 25000, 40000)):
        await make_listing(session, price=price, seq=seq + 30)

    resp = await partner_client.get(
        "/api/partner/market",
        params={"make": "Kia", "limit": 5},
        headers={"X-API-Key": raw},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 6
    assert body["min_price"] == 9000
    assert body["max_price"] == 40000
    assert body["median_price"] == 20000
    assert [deal["price"] for deal in body["deals"]] == [9000, 15000]
    assert body["deals"][0]["discount_pct"] == 55.0
    assert body["deals"][0]["url"].startswith("https://example.test/")


@pytest.mark.asyncio
async def test_partner_market_endpoint_requires_api_key(partner_client):
    """Без X-API-Key рынок недоступен (поверхность только для партнёров)."""
    resp = await partner_client.get("/api/partner/market")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_partner_market_endpoint_empty_market(partner_client, session):
    """Пустой каталог — валидный пустой ответ, не 500."""
    _, raw = await make_partner(session)

    resp = await partner_client.get(
        "/api/partner/market", headers={"X-API-Key": raw}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["median_price"] is None
    assert body["deals"] == []

