# tests/adapters/test_encar.py
import httpx
import pytest

from app.infrastructure.sources.encar import EncarApiAdapter
from pydantic import ValidationError

from app.infrastructure.sources.base import FetchError
from app.infrastructure.sources.common.http import HttpClient
from app.domain.search.criteria import SearchCriteria


# --- _map_item: crash-safety ----------------------------------------

def _adapter(**config):
    return EncarApiAdapter(source_name="encar", config=config)


def _criteria(**overrides) -> SearchCriteria:
    return SearchCriteria(**overrides)


def _dummy_criteria() -> SearchCriteria:
    return SearchCriteria()


@pytest.mark.parametrize("mileage", ["invalid", "52,000", "52000 km", None, 0])
def test_map_item_mileage_safe(mileage):
    a = _adapter()
    item = a._map_item({"Id": 1, "Mileage": mileage})
    assert item is not None
    assert item.mileage_km in (None, 0) or isinstance(item.mileage_km, int)


@pytest.mark.parametrize("price", ["invalid", "12,000", None, "", 12000])
def test_map_item_price_safe(price):
    a = _adapter()
    item = a._map_item({"Id": 1, "Price": price})
    assert item is not None


@pytest.mark.parametrize("year", ["invalid", "2022", 202208.0, 2022, None, "202208"])
def test_map_item_year_safe(year):
    a = _adapter()
    item = a._map_item({"Id": 1, "Year": year})
    assert item is not None
    if item.year is not None:
        assert 1900 <= item.year <= 2100


def test_map_item_invalid_id_returns_none():
    a = _adapter()
    assert a._map_item({"Id": "abc"}) is None
    assert a._map_item({"Id": None}) is None


def test_map_item_vin_uppercased_and_stripped():
    a = _adapter()
    item = a._map_item({"Id": 1, "Vin": "  vin123  "})
    assert item.vin == "VIN123"


def test_map_item_body_and_color_normalized():
    a = _adapter()
    item = a._map_item({"Id": 1, "BodyType": "suv", "Color": "blue"})
    assert item.body_type == "suv"
    assert item.color == "blue"


# --- fetch: сломанный контракт ---------------------------------------

@pytest.mark.asyncio
async def test_fetch_broken_contract_logs_warning(caplog):
    """Count > 0, но SearchResults пуст → warning, items=[], has_more=False."""
    def handler(request):
        return httpx.Response(200, json={"Count": 1000, "SearchResults": []})
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    with caplog.at_level("WARNING"):
        res = await a.fetch(criteria=_dummy_criteria())
    assert res.items == []
    assert res.has_more is False
    assert "encar_broken_contract" in caplog.text


@pytest.mark.asyncio
async def test_fetch_search_results_null_does_not_crash():
    def handler(request):
        return httpx.Response(200, json={"Count": 0, "SearchResults": None})
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    res = await a.fetch(criteria=_dummy_criteria())
    assert res.items == []


@pytest.mark.asyncio
async def test_fetch_invalid_cursor_raises():
    a = _adapter()
    with pytest.raises(FetchError, match="invalid cursor"):
        await a.fetch(criteria=_dummy_criteria(), cursor="abc")


@pytest.mark.asyncio
async def test_fetch_negative_cursor_raises():
    a = _adapter()
    with pytest.raises(FetchError, match=">= 0"):
        await a.fetch(criteria=_dummy_criteria(), cursor="-1")


# --- _build_q: silent drop fix ---------------------------------------

def test_unknown_make_raises():
    a = _adapter()
    with pytest.raises(FetchError, match="not supported"):
        a._build_q(_criteria(make="Ioniq"))


def test_unknown_model_raises():
    a = _adapter()
    with pytest.raises(FetchError, match="not supported"):
        a._build_q(_criteria(model="unknown-model"))


def test_known_make_included():
    a = _adapter()
    q = a._build_q(_criteria(make="Hyundai"))
    assert "Manufacturer.현대" in q


# --- _price_range_man: currency / inversion --------------------------

def test_eur_currency_raises():
    a = _adapter()
    with pytest.raises(FetchError, match="currency"):
        a._price_range_man(_criteria(currency="EUR", price_min=100))


def test_price_min_greater_than_max_raises():
    # Инвариант проверяется на входе (SearchCriteria), а не в адаптере:
    # невалидные критерии не должны доходить до парсера.
    with pytest.raises(ValidationError, match="price_min"):
        _criteria(price_min=20000, price_max=10000)


def test_price_range_uses_round_for_max():
    a = _adapter(usd_krw=1400)
    lo, hi = a._price_range_man(_criteria(currency="USD", price_min=100, price_max=19999))
    # 19999 * 1400 / 10000 = 2799.86 → round → 2800 (не 2799)
    assert hi == 2800


# --- healthcheck ----------------------------------------------------

@pytest.mark.asyncio
async def test_healthcheck_broken_contract_returns_false():
    def handler(request):
        return httpx.Response(200, json={"Count": 1000, "SearchResults": []})
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    assert await a.healthcheck() is False


@pytest.mark.asyncio
async def test_healthcheck_empty_count_zero_returns_true():
    def handler(request):
        return httpx.Response(200, json={"Count": 0, "SearchResults": []})
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    assert await a.healthcheck() is True


# --- init validation -------------------------------------------------

def test_page_size_zero_rejected():
    with pytest.raises(ValueError, match="page_size"):
        EncarApiAdapter(source_name="encar", config={"page_size": 0})


def test_usd_krw_zero_rejected():
    with pytest.raises(ValueError, match="usd_krw"):
        EncarApiAdapter(source_name="encar", config={"usd_krw": 0})