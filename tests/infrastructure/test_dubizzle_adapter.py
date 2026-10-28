"""Контрактные тесты DubizzleHtmlAdapter (ОАЭ, SSR-парсинг → UnifiedListing).

Фикстуры — мини-фрагменты HTML по структуре OLX-вёрстки dubizzle (два
поколения: data-aut-id и data-testid). Живой сайт закрыт Imperva-челленджем
(2026-10-28, см. докстринг модуля), поэтому селекторы проверены только
здесь; перед включением источника — сверить с живым HTML.
"""

from decimal import Decimal

import httpx
import pytest

from app.infrastructure.sources.dubizzle import (
    DubizzleHtmlAdapter,
    extract_make_en,
    parse_mileage,
    parse_price,
)
from app.infrastructure.sources.common.http import FetchError, HttpClient
from app.domain.search.criteria import SearchCriteria

# Поколение вёрстки OLX data-aut-id: марка/модель из заголовка, цена AED,
# пробег в милях (конвертация ×1.60934), город в отдельном узле.
CARD = """
<li data-aut-id="itemCard">
  <a data-aut-id="itemLink" href="/motors/used-cars/toyota/land-cruiser-2021-6732541/">
    <img src="https://images.dubizzle.com/thumbs/2024/06/6732541_1.jpg"
         data-src="https://images.dubizzle.com/thumbs/2024/06/6732541_1.jpg">
    <h2 data-aut-id="itemTitle">Toyota Land Cruiser 2021 5.7L VXR</h2>
    <span data-aut-id="itemPrice" dir="ltr">AED 125,000</span>
    <div data-aut-id="item-location">Dubai &bull; 23 minutes ago</div>
    <div class="specs">45,000 mi &middot; Automatic &middot; GCC</div>
  </a>
</li>
"""

# Поколение data-testid (редизайн): absolute href, data-id на карточке,
# пробег в км (без конвертации), город без таймстемпа.
CARD_V2 = """
<li data-testid="listing-card" data-id="5309118">
  <a href="https://www.dubizzle.com/motors/used-cars/nissan/patrol-2020-5309118/">
    <h3>Nissan Patrol 2020 Platinum</h3>
    <span data-testid="ad-price">AED 189,500</span>
  </a>
  <div data-testid="ad-item-location">Abu Dhabi</div>
  <div class="specs">123,456 km</div>
</li>
"""

CARD_NO_ID = """
<li data-aut-id="itemCard">
  <a href="/motors/used-cars/"><h2 data-aut-id="itemTitle">без id</h2></a>
</li>
"""

# Мини-фрагмент РЕАЛЬНОГО ответа dubizzle на GET списка (2026-10-28):
# Imperva-челлендж отдаёт HTTP 200, но вместо карточек — JS-стена.
IMPERVA_WALL = """<html><head>
    <noscript><title>Pardon Our Interruption</title></noscript>
    <meta name="robots" content="noindex, nofollow">
</head><body>
    <script>window.reeseSkipExpirationCheck = true;</script>
</body></html>"""


def _page(*cards: str) -> str:
    return f"<html><body><ul>{''.join(cards)}</ul></body></html>"


def make_adapter(
    pages: list[str], **kwargs
) -> tuple[DubizzleHtmlAdapter, list[httpx.Request]]:
    """Адаптер с MockTransport: отдаёт HTML-страницы по очереди."""
    requests: list[httpx.Request] = []
    queue = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = queue.pop(0) if queue else _page()
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    adapter = DubizzleHtmlAdapter(
        source_name="dubizzle",
        http_client=HttpClient(transport=transport, timeout=10),
        **kwargs,
    )
    adapter.requests = requests  # type: ignore[attr-defined]
    return adapter, requests


# --- helpers ---------------------------------------------------------


def test_parse_price():
    assert parse_price("AED 125,000") == Decimal("125000")
    assert parse_price("AED 189,500 negotiable") == Decimal("189500")
    assert parse_price("AED 59,750") == Decimal("59750")
    assert parse_price("Price on request") is None
    assert parse_price("") is None
    assert parse_price(None) is None


def test_parse_mileage_mi_and_km():
    assert parse_mileage("45,000 mi") == 72420  # ×1.60934 → 72 420,3 → 72 420
    assert parse_mileage("45,000 miles") == 72420
    assert parse_mileage("123,456 km") == 123456
    assert parse_mileage("123,456 kms") == 123456
    # «mi» внутри «minutes» не матчится (word boundary)
    assert parse_mileage("Dubai • 23 minutes ago") is None
    assert parse_mileage("нет пробега") is None
    assert parse_mileage(None) is None


def test_extract_make_en():
    assert extract_make_en("Toyota Land Cruiser 2021 5.7L VXR") == ("Toyota", "Land Cruiser")
    assert extract_make_en("Nissan Patrol 2020 Platinum") == ("Nissan", "Patrol")
    # longest-match: «Land Rover» раньше коротких ключей; модель «Range Rover»
    assert extract_make_en("Land Rover Range Rover 2020") == ("Land Rover", "Range Rover")
    assert extract_make_en("Mercedes-Benz GLE 2022") == ("Mercedes-Benz", "GLE")
    assert extract_make_en("безмарочный заголовок") == (None, None)
    assert extract_make_en(None) == (None, None)


# --- fetch / parsing -------------------------------------------------


async def test_fetch_maps_card_to_unified_listing():
    adapter, requests = make_adapter([_page(CARD)])
    result = await adapter.fetch(SearchCriteria())

    assert len(requests) == 1
    assert "dubizzle.com" in str(requests[0].url)
    assert requests[0].headers["Accept-Language"].startswith("en-US")

    assert len(result.items) == 1
    item = result.items[0]
    assert item.source == "dubizzle"
    assert item.source_id == "6732541"  # хвост ссылки -<id>/
    assert str(item.url) == (
        "https://www.dubizzle.com/motors/used-cars/toyota/land-cruiser-2021-6732541/"
    )
    assert item.title == "Toyota Land Cruiser 2021 5.7L VXR"
    assert item.make == "Toyota"
    assert item.model == "Land Cruiser"  # слова до года, не «VXR»
    assert item.year == 2021
    assert item.price == Decimal("125000")  # AED 125,000 → 125000
    assert item.currency == "AED"
    assert item.mileage_km == 72420  # 45,000 mi → км
    assert item.transmission == "automatic"  # Automatic → detect_transmission
    assert item.location == "Dubai"
    assert item.vin is None  # VIN в списке dubizzle нет
    assert item.country == "AE"  # страна в UnifiedListing (GEO-V1 §1)
    assert item.raw["country"] == "AE"  # дубль в raw — до бэкфилда поля
    assert [str(i) for i in item.images] == [
        "https://images.dubizzle.com/thumbs/2024/06/6732541_1.jpg"
    ]
    assert item.raw["title"] == item.title  # raw_payload сохраняется
    assert "Land Cruiser" in item.raw["html"]
    await adapter.close()


async def test_fetch_maps_data_testid_generation():
    """Редизайн data-testid: data-id, absolute href, пробег в км."""
    adapter, _ = make_adapter([_page(CARD_V2)])
    result = await adapter.fetch(SearchCriteria())

    item = result.items[0]
    assert item.source_id == "5309118"
    assert item.make == "Nissan"
    assert item.model == "Patrol"
    assert item.year == 2020
    assert item.price == Decimal("189500")
    assert item.mileage_km == 123456  # km без конвертации
    assert item.location == "Abu Dhabi"
    assert str(item.url) == (
        "https://www.dubizzle.com/motors/used-cars/nissan/patrol-2020-5309118/"
    )
    await adapter.close()


async def test_fetch_skips_cards_without_id():
    adapter, _ = make_adapter([_page(CARD_NO_ID, CARD)])
    result = await adapter.fetch(SearchCriteria())
    assert len(result.items) == 1
    assert result.items[0].source_id == "6732541"
    await adapter.close()


async def test_fetch_broken_card_does_not_break_page():
    # карточка без узлов → исключение внутри _map_card → skip с warning
    broken = '<li data-aut-id="itemCard"><div>мусор</div></li>'
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
    assert "page=2" in str(requests[1].url)

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


async def test_healthcheck_false_on_imperva_wall():
    """Imperva отдаёт 200 с JS-стеной вместо карточек — healthcheck False."""
    adapter, _ = make_adapter([IMPERVA_WALL])
    assert await adapter.healthcheck() is False
    await adapter.close()


async def test_healthcheck_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    adapter = DubizzleHtmlAdapter(
        source_name="dubizzle",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    assert await adapter.healthcheck() is False
    await adapter.close()


async def test_fetch_raises_fetch_error_on_403():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    adapter = DubizzleHtmlAdapter(
        source_name="dubizzle",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    with pytest.raises(FetchError):  # геоблок/анти-бот отдаёт 403
        await adapter.fetch(SearchCriteria())
    await adapter.close()
