# tests/adapters/test_che168.py
import httpx
import pytest
from decimal import Decimal

from app.infrastructure.sources.che168 import (
    HEADERS,
    Che168HtmlAdapter,
    _safe_join,
    extract_make_zh,
    parse_wan_number,
    _PRICE_WAN_RE,
    _PRICE_PLAIN_RE,
)
from app.infrastructure.sources.common.http import FetchError, HttpClient
from app.domain.search.criteria import SearchCriteria


def _dummy_criteria() -> SearchCriteria:
    return SearchCriteria()


# --- price: регрессия «год как цена» ---------------------------------

def _html_with_cards(cards: str, card_selector="ul#usedList li") -> str:
    return f'<html><body><ul id="usedList">{cards}</ul></body></html>'


def _card(href="/a/71085432/", title="2020款 丰田汉兰达", price="23.80万", mileage="5.2万公里"):
    return (
        f'<li data-id="71085432">'
        f'<a href="{href}"><h4>{title}</h4></a>'
        f'<span class="price">{price}</span>'
        f'<span class="info">{mileage}</span>'
        f'</li>'
    )


@pytest.mark.asyncio
async def test_price_fallback_does_not_match_year():
    """Селектор .price не найден → цена из текста, только с 万."""
    # Убираем .price из карточки — эмулируем смену вёрстки
    card = (
        '<li data-id="1"><a href="/a/1/"><h4>2020款 丰田</h4></a>'
        '<span class="info">23.80万 5.2万公里</span></li>'
    )
    def handler(request):
        return httpx.Response(200, text=_html_with_cards(card))
    client = HttpClient(transport=httpx.MockTransport(handler))
    adapter = Che168HtmlAdapter(source_name="che168", http_client=client)
    res = await adapter.fetch(criteria=_dummy_criteria())
    assert len(res.items) == 1
    assert res.items[0].price == Decimal("238000")  # не 2020!


@pytest.mark.asyncio
async def test_price_no_wan_in_text_returns_none():
    """Без цены в тексте (только «2020款») — price=None, не 2020."""
    card = '<li data-id="1"><a href="/a/1/"><h4>2020款 丰田</h4></a></li>'
    def handler(request):
        return httpx.Response(200, text=_html_with_cards(card))
    client = HttpClient(transport=httpx.MockTransport(handler))
    adapter = Che168HtmlAdapter(source_name="che168", http_client=client)
    res = await adapter.fetch(criteria=_dummy_criteria())
    assert res.items[0].price is None


# --- URLs: безопасность ---------------------------------------------

@pytest.mark.parametrize("href", [
    "javascript:alert(1)",
    "data:text/html,<script>",
    "vbscript:msgbox",
    "//evil.com/a/1",
    "",
    None,
])
def test_safe_join_rejects_bad_schemes(href):
    assert _safe_join("https://www.che168.com/", href) is None


@pytest.mark.parametrize("href", ["/a/123/", "https://www.che168.com/a/123/"])
def test_safe_join_accepts_http(href):
    assert _safe_join("https://www.che168.com/", href) is not None


@pytest.mark.asyncio
async def test_unsafe_href_skips_listing():
    """Без безопасного URL карточка бесполезна: listings.url — NOT NULL."""
    card = '<li data-id="1"><a href="javascript:alert(1)"><h4>X</h4></a></li>'
    def handler(request):
        return httpx.Response(200, text=_html_with_cards(card))
    client = HttpClient(transport=httpx.MockTransport(handler))
    adapter = Che168HtmlAdapter(source_name="che168", http_client=client)
    res = await adapter.fetch(criteria=_dummy_criteria())
    assert res.items == []


@pytest.mark.asyncio
async def test_images_filter_javascript_scheme():
    card = (
        '<li data-id="1"><a href="/a/1/"></a>'
        '<img src="javascript:alert(1)">'
        '<img src="/img/a.jpg">'
        '</li>'
    )
    def handler(request):
        return httpx.Response(200, text=_html_with_cards(card))
    client = HttpClient(transport=httpx.MockTransport(handler))
    adapter = Che168HtmlAdapter(source_name="che168", http_client=client)
    res = await adapter.fetch(criteria=_dummy_criteria())
    assert [str(u) for u in res.items[0].images] == ["https://www.che168.com/img/a.jpg"]


# --- cursor ----------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_cursor_raises_fetch_error():
    adapter = Che168HtmlAdapter(source_name="che168")
    with pytest.raises(FetchError, match="invalid cursor"):
        await adapter.fetch(criteria=_dummy_criteria(), cursor="abc")


@pytest.mark.asyncio
async def test_negative_cursor_raises():
    adapter = Che168HtmlAdapter(source_name="che168")
    with pytest.raises(FetchError, match=">= 1"):
        await adapter.fetch(criteria=_dummy_criteria(), cursor="-1")


# --- source_id ------------------------------------------------------

@pytest.mark.asyncio
async def test_data_id_validated_as_numeric():
    card = '<li data-id="not-a-number"><a href="/a/71085432/"></a></li>'
    def handler(request):
        return httpx.Response(200, text=_html_with_cards(card))
    client = HttpClient(transport=httpx.MockTransport(handler))
    adapter = Che168HtmlAdapter(source_name="che168", http_client=client)
    res = await adapter.fetch(criteria=_dummy_criteria())
    # fallback на _ID_RE из href
    assert res.items[0].source_id == "71085432"


# --- misc -----------------------------------------------------------

def test_extract_make_zh_long_key_wins():
    assert extract_make_zh("广汽传祺GS4") == "Trumpchi"  # не Geely/etc.


def test_parse_wan_number_strict():
    assert parse_wan_number("23.80万", _PRICE_WAN_RE) == Decimal("238000")
    assert parse_wan_number("2020", _PRICE_WAN_RE) is None
    assert parse_wan_number("238000", _PRICE_PLAIN_RE) == Decimal("238000")


def test_max_pages_validated(monkeypatch):
    with pytest.raises(ValueError, match="max_pages"):
        Che168HtmlAdapter(
            source_name="che168", config={"max_pages": "abc"}
        )


def test_max_pages_clamped_to_one():
    a = Che168HtmlAdapter(source_name="che168", config={"max_pages": "0"})
    assert a._max_pages == 1


def test_headers_not_mutable():
    with pytest.raises(TypeError):
        HEADERS["X"] = "y"  # MappingProxyType