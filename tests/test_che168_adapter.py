"""Контрактные тесты Che168HtmlAdapter (Китай, SSR-парсинг → UnifiedListing)."""

import re
from decimal import Decimal

import httpx
import pytest

from app.adapters.che168 import (
    Che168HtmlAdapter,
    extract_make_zh,
    parse_wan_number,
)
from app.adapters.common.http import FetchError, HttpClient
from app.schemas.criteria import SearchCriteria

CARD = """
<li class="caritem" data-id="71085432">
  <a class="carinfo" href="/a/71085432/">
    <img src="//img.che168.com/auto/2024/1_1.jpg" data-src="//img.che168.com/auto/2024/1.jpg">
    <h4 class="t">2020款 丰田汉兰达 2.0T 四驱精英版</h4>
    <ul class="msg"><li>5.2万公里</li><li>2020年上牌</li><li>昆明</li><li>自动挡</li></ul>
    <div class="pricebox"><span class="price">23.80万</span></div>
  </a>
</li>
"""

CARD_NO_ID = """
<li class="caritem">
  <a class="carinfo" href="/dealer/"><h4 class="t">без id</h4></a>
</li>
"""

ANY_NUM = re.compile(r"([\d.]+)\s*(万?)")


def _page(*cards: str) -> str:
    return f"<html><body><ul id='usedList'>{''.join(cards)}</ul></body></html>"


def make_adapter(
    pages: list[str], **kwargs
) -> tuple[Che168HtmlAdapter, list[httpx.Request]]:
    """Адаптер с MockTransport: отдаёт HTML-страницы по очереди."""
    requests: list[httpx.Request] = []
    queue = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = queue.pop(0) if queue else _page()
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    adapter = Che168HtmlAdapter(
        source_name="che168",
        http_client=HttpClient(transport=transport, timeout=10),
        **kwargs,
    )
    adapter.requests = requests  # type: ignore[attr-defined]
    return adapter, requests


# --- helpers ---------------------------------------------------------


def test_parse_wan_price():
    assert parse_wan_number("23.80万", ANY_NUM) == Decimal("238000")
    assert parse_wan_number("238000", ANY_NUM) == Decimal("238000")
    assert parse_wan_number("нет цены", ANY_NUM) is None
    assert parse_wan_number(None, ANY_NUM) is None


def test_extract_make_zh():
    assert extract_make_zh("2020款 丰田汉兰达 2.0T") == "Toyota"
    assert extract_make_zh("比亚迪汉 EV") == "BYD"
    assert extract_make_zh("广汽传祺GS4") == "Trumpchi"  # длинный ключ раньше короткого
    assert extract_make_zh("какой-то текст без марки") is None
    assert extract_make_zh(None) is None


# --- fetch / parsing -------------------------------------------------


async def test_fetch_maps_card_to_unified_listing():
    adapter, requests = make_adapter([_page(CARD)])
    result = await adapter.fetch(SearchCriteria())

    assert len(requests) == 1
    assert "che168.com" in str(requests[0].url)
    assert requests[0].headers["Accept-Language"].startswith("zh-CN")

    assert len(result.items) == 1
    item = result.items[0]
    assert item.source == "che168"
    assert item.source_id == "71085432"
    assert str(item.url) == "https://www.che168.com/a/71085432/"
    assert item.title == "2020款 丰田汉兰达 2.0T 四驱精英版"
    assert item.make == "Toyota"  # 丰田 → Toyota
    assert item.year == 2020
    assert item.price == Decimal("238000")  # 23.80万 → 238000 CNY
    assert item.currency == "CNY"
    assert item.mileage_km == 52000  # 5.2万公里 → 52000
    assert item.transmission == "automatic"  # 自动挡 → automatic
    assert item.vin is None  # VIN в Китае скрыт
    assert str(item.images[0]).endswith("auto/2024/1.jpg")  # data-src, абсолютный
    assert item.raw["title"] == item.title  # raw_payload сохраняется
    assert "丰田" in item.raw["html"]
    await adapter.close()


async def test_fetch_skips_cards_without_id():
    adapter, _ = make_adapter([_page(CARD_NO_ID, CARD)])
    result = await adapter.fetch(SearchCriteria())
    assert len(result.items) == 1
    await adapter.close()


async def test_fetch_broken_card_does_not_break_page():
    # карточка без ссылки-селектора → исключение внутри _map_card → skip
    broken = "<li class='caritem'><h4 class='t'>x</h4><span class='price'>1万</span></li>"
    adapter, _ = make_adapter([_page(broken, CARD)])
    result = await adapter.fetch(SearchCriteria())
    assert len(result.items) == 1
    await adapter.close()


async def test_pagination():
    adapter, requests = make_adapter([_page(CARD), _page(CARD), _page(CARD)])
    first = await adapter.fetch(SearchCriteria(), cursor=None)
    assert first.has_more is True
    assert first.next_cursor == "2"

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
    await adapter.close()


# --- healthcheck / errors ---------------------------------------------


async def test_healthcheck_true_with_cards():
    adapter, _ = make_adapter([_page(CARD)])
    assert await adapter.healthcheck() is True
    await adapter.close()


async def test_healthcheck_false_on_empty_layout():
    adapter, _ = make_adapter(["<html><body>captcha</body></html>"])
    assert await adapter.healthcheck() is False
    await adapter.close()


async def test_healthcheck_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    adapter = Che168HtmlAdapter(
        source_name="che168",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    assert await adapter.healthcheck() is False
    await adapter.close()


async def test_fetch_raises_fetch_error_on_403():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    adapter = Che168HtmlAdapter(
        source_name="che168",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    with pytest.raises(FetchError):  # геоблок/анти-бот отдаёт 403
        await adapter.fetch(SearchCriteria())
    await adapter.close()


async def test_registry_has_che168():
    from app.adapters import ADAPTER_CLASSES

    assert ADAPTER_CLASSES["Che168HtmlAdapter"] is Che168HtmlAdapter
