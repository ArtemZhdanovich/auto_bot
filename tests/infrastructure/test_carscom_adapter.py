"""Контрактные тесты CarsComHtmlAdapter (США, SSR-парсинг → UnifiedListing).

Фикстуры — мини-фрагменты HTML по структуре листинга cars.com (классические
классы vehicle-card + data-qa варианты). Живой сайт отдаёт 403 (Akamai,
2026-10-28, см. докстринг модуля), поэтому селекторы проверены только здесь;
перед включением источника — сверить с живым HTML.
"""

from decimal import Decimal

import httpx
import pytest

from app.infrastructure.sources.cars_com import (
    CarsComHtmlAdapter,
    extract_make_en,
    parse_location,
    parse_mileage,
    parse_price,
)
from app.infrastructure.sources.common.http import FetchError, HttpClient
from app.domain.search.criteria import SearchCriteria

# Классическая вёрстка: год в начале заголовка, цена $, пробег в милях,
# локация «City, ST» дилера, фото на внешнем CDN дилера.
CARD = """
<div class="vehicle-card" data-listing-id="923165820">
  <a class="vehicle-card-link" href="/vehicledetail/detail/923165820/">
    <span class="title">Used 2021 Toyota Camry SE</span>
  </a>
  <img class="vehicle-card-image"
       data-lazy-load="https://pictures.dealer.com/t/toyota/923165820_1.jpg"
       src="">
  <img src="//evil.com/track.jpg">
  <span class="primary-price">$24,998</span>
  <div class="mileage">45,000 mi.</div>
  <div class="dealer-location">Scottsdale, AZ 85254</div>
</div>
"""

# Вариант data-qa: UUID в URL, «Not Priced» → price=None, электрокар.
CARD_V2 = """
<div data-qa="vehicle-card">
  <a href="/vehicledetail/detail/8f2a1b3c-9d4e-4c5b-8a6f-1e2d3c4b5a6f/">
    <h2 class="title">Used 2023 Hyundai Ioniq 5 Limited</h2>
  </a>
  <span data-qa="primary-price">Not Priced</span>
  <div data-qa="mileage">12,500 mi</div>
  <div data-qa="dealer-location">Austin, TX</div>
  <span class="badge">Electric</span>
</div>
"""

CARD_NO_ID = """
<div class="vehicle-card">
  <a href="/shopping/some-promo/"><span class="title">без id</span></a>
</div>
"""

# Мини-фрагмент РЕАЛЬНОГО ответа cars.com на GET списка (2026-10-28):
# анти-бот Akamai отдаёт 403 с Reference-страницей вместо списка.
AKAMAI_403 = """<html><body>
<h1>Access Denied</h1>
<p>You don't have permission to access this resource.</p>
<p>Reference #18.abcdef.1234.1603900000</p>
</body></html>"""


def _page(*cards: str) -> str:
    return f"<html><body><div class='results'>{''.join(cards)}</div></body></html>"


def make_adapter(
    pages: list[str], **kwargs
) -> tuple[CarsComHtmlAdapter, list[httpx.Request]]:
    """Адаптер с MockTransport: отдаёт HTML-страницы по очереди."""
    requests: list[httpx.Request] = []
    queue = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = queue.pop(0) if queue else _page()
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    adapter = CarsComHtmlAdapter(
        source_name="cars_com",
        http_client=HttpClient(transport=transport, timeout=10),
        **kwargs,
    )
    adapter.requests = requests  # type: ignore[attr-defined]
    return adapter, requests


# --- helpers ---------------------------------------------------------


def test_parse_price():
    assert parse_price("$24,998") == Decimal("24998")
    assert parse_price("$1,150,000") == Decimal("1150000")
    assert parse_price("24,998") == Decimal("24998")
    assert parse_price("Not Priced") is None
    assert parse_price("") is None
    assert parse_price(None) is None


def test_parse_mileage_miles_to_km():
    assert parse_mileage("45,000 mi.") == 72420  # ×1.60934 → 72 420,3 → 72 420
    assert parse_mileage("12,500 mi") == 20117  # 12 500 × 1.60934 = 20 116,75
    assert parse_mileage("1 mile") == 2  # 1 × 1.60934 → 1.60934 → 2
    assert parse_mileage("45,000 km") is None  # cars.com отдаёт только мили
    assert parse_mileage(None) is None


def test_parse_location_usps_state():
    assert parse_location("Scottsdale, AZ 85254") == "Scottsdale, AZ"
    assert parse_location("Austin, TX") == "Austin, TX"
    assert parse_location("Washington, DC") == "Washington, DC"
    assert parse_location("Sellers Motors") is None  # без штата
    assert parse_location(None) is None


def test_extract_make_en():
    # год в начале (формат cars.com) игнорируется, модель — до стоп-слова
    assert extract_make_en("Used 2021 Toyota Camry SE") == ("Toyota", "Camry SE")
    assert extract_make_en("2023 Ford Explorer XLT") == ("Ford", "Explorer XLT")
    # longest-match: «Land Rover» раньше коротких ключей
    assert extract_make_en("2019 Land Rover Range Rover Sport") == (
        "Land Rover", "Range Rover Sport",
    )
    assert extract_make_en("Mercedes-Benz C-Class 2020") == ("Mercedes-Benz", None)
    assert extract_make_en("Unknown Make 2020") == (None, None)
    assert extract_make_en(None) == (None, None)


# --- fetch / parsing -------------------------------------------------


async def test_fetch_maps_card_to_unified_listing():
    adapter, requests = make_adapter([_page(CARD)])
    result = await adapter.fetch(SearchCriteria())

    assert len(requests) == 1
    url = str(requests[0].url)
    assert "cars.com/shopping/results/" in url
    assert "stock_type=used" in url  # базовые параметры не потеряны
    assert requests[0].headers["Accept-Language"].startswith("en-US")

    assert len(result.items) == 1
    item = result.items[0]
    assert item.source == "cars_com"
    assert item.source_id == "923165820"  # data-listing-id / хвост ссылки
    assert str(item.url) == (
        "https://www.cars.com/vehicledetail/detail/923165820/"
    )
    assert item.title == "Used 2021 Toyota Camry SE"
    assert item.make == "Toyota"
    assert item.model == "Camry SE"  # best effort: трим попадает в модель
    assert item.year == 2021
    assert item.price == Decimal("24998")  # $24,998 → 24998
    assert item.currency == "USD"
    assert item.mileage_km == 72420  # 45,000 mi → км
    assert item.location == "Scottsdale, AZ"
    assert item.vin is None  # VIN в списке cars.com нет
    assert item.seller_type == "dealer"
    assert item.country == "US"  # страна в UnifiedListing (GEO-V1 §1)
    assert item.raw["country"] == "US"  # дубль в raw — до бэкфилда поля
    # CDN-фото дилера разрешены allowlist'ом; //evil.com — отброшен
    assert [str(i) for i in item.images] == [
        "https://pictures.dealer.com/t/toyota/923165820_1.jpg"
    ]
    assert item.raw["title"] == item.title  # raw_payload сохраняется
    assert "Camry" in item.raw["html"]
    await adapter.close()


async def test_fetch_maps_data_qa_generation():
    """Вариант data-qa: UUID id, Not Priced → None, EV-бейдж → fuel."""
    adapter, _ = make_adapter([_page(CARD_V2)])
    result = await adapter.fetch(SearchCriteria())

    item = result.items[0]
    assert item.source_id == "8f2a1b3c-9d4e-4c5b-8a6f-1e2d3c4b5a6f"
    assert item.make == "Hyundai"
    assert item.model == "Ioniq"  # «5» (не-буква) обрывает модель
    assert item.year == 2023
    assert item.price is None  # Not Priced
    assert item.currency == "USD"  # валюта известна и без цены
    assert item.mileage_km == 20117  # 12,500 mi → км
    assert item.location == "Austin, TX"
    assert item.fuel == "electric"  # бейдж Electric → detect_fuel
    await adapter.close()


async def test_fetch_skips_cards_without_id():
    adapter, _ = make_adapter([_page(CARD_NO_ID, CARD)])
    result = await adapter.fetch(SearchCriteria())
    assert len(result.items) == 1
    assert result.items[0].source_id == "923165820"
    await adapter.close()


async def test_fetch_broken_card_does_not_break_page():
    # карточка без узлов → исключение внутри _map_card → skip с warning
    broken = '<div class="vehicle-card"><div>мусор</div></div>'
    adapter, _ = make_adapter([_page(broken, CARD)])
    result = await adapter.fetch(SearchCriteria())
    assert len(result.items) == 1
    await adapter.close()


async def test_pagination():
    adapter, requests = make_adapter([_page(CARD), _page(CARD), _page(CARD)])
    first = await adapter.fetch(SearchCriteria(), cursor=None)
    assert first.has_more is True
    assert first.next_cursor == "2"
    assert first.source_meta["page"] == 1

    second = await adapter.fetch(SearchCriteria(), cursor=first.next_cursor)
    second_url = str(requests[1].url)
    assert "page=2" in second_url
    assert "stock_type=used" in second_url  # page не затёр базовые параметры

    # по умолчанию max_pages=5 → после 2-й страницы ещё есть
    assert second.has_more is True
    await adapter.close()


async def test_max_pages_from_config():
    adapter, _ = make_adapter([_page(CARD)], config={"max_pages": 1})
    result = await adapter.fetch(SearchCriteria())
    assert result.has_more is False
    assert result.next_cursor is None
    await adapter.close()


async def test_empty_page_no_more():
    adapter, _ = make_adapter([_page()])
    result = await adapter.fetch(SearchCriteria())
    assert result.items == []
    assert result.has_more is False
    assert result.source_meta["parsed"] == 0
    await adapter.close()


async def test_invalid_cursor_raises():
    adapter, _ = make_adapter([_page(CARD)])
    with pytest.raises(FetchError, match="invalid cursor"):
        await adapter.fetch(SearchCriteria(), cursor="abc")
    adapter2, _ = make_adapter([_page(CARD)])
    with pytest.raises(FetchError, match=">= 1"):
        await adapter2.fetch(SearchCriteria(), cursor="0")
    await adapter.close()
    await adapter2.close()


# --- healthcheck / errors ---------------------------------------------


async def test_healthcheck_true_with_cards():
    adapter, _ = make_adapter([_page(CARD)])
    assert await adapter.healthcheck() is True
    await adapter.close()


async def test_healthcheck_false_on_403_wall():
    """403 анти-бота → healthcheck False (прокси/селекторы не работают)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=AKAMAI_403)

    adapter = CarsComHtmlAdapter(
        source_name="cars_com",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    assert await adapter.healthcheck() is False
    await adapter.close()


async def test_healthcheck_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    adapter = CarsComHtmlAdapter(
        source_name="cars_com",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    assert await adapter.healthcheck() is False
    await adapter.close()


async def test_fetch_raises_fetch_error_on_403():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    adapter = CarsComHtmlAdapter(
        source_name="cars_com",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    with pytest.raises(FetchError):  # анти-бот Akamai отдаёт 403
        await adapter.fetch(SearchCriteria())
    await adapter.close()
