# tests/adapters/test_kcar.py
import httpx
import pytest

from app.infrastructure.sources.kcar import KCarApiAdapter, _encrypt_params
from pydantic import ValidationError

from app.infrastructure.sources.base import FetchError
from app.infrastructure.sources.common.http import HttpClient
from app.domain.search.criteria import SearchCriteria


def _adapter(**config):
    return KCarApiAdapter(source_name="kcar", config=config)


def _criteria(**overrides) -> SearchCriteria:
    return SearchCriteria(**overrides)


def _dummy_criteria() -> SearchCriteria:
    return SearchCriteria()


# --- headers: регрессия «без Referer/UA → 404» -----------------------

@pytest.mark.asyncio
async def test_fetch_sends_required_headers():
    captured = {}
    def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"data": {"rows": [], "totalCnt": 0}})
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    await a.fetch(criteria=_dummy_criteria())
    assert "referer" in captured["headers"]
    assert "user-agent" in captured["headers"]


# --- _map_item: crash-safety ----------------------------------------

@pytest.mark.parametrize("price", ["invalid", "12,000", None, "", 12000])
def test_map_item_price_safe(price):
    item = _adapter()._map_item({"carCd": 1, "prc": price})
    assert item is not None


@pytest.mark.parametrize("year", ["invalid", "2022-01", 2022, None])
def test_map_item_year_safe(year):
    item = _adapter()._map_item({"carCd": 1, "prdcnYr": year})
    assert item is not None


@pytest.mark.parametrize("mileage", ["52,000", "52000 km", None, 52000])
def test_map_item_mileage_safe(mileage):
    item = _adapter()._map_item({"carCd": 1, "milg": mileage})
    assert item is not None


def test_map_item_invalid_id_returns_none():
    assert _adapter()._map_item({"carCd": "abc"}) is None
    assert _adapter()._map_item({"carCd": None}) is None


# --- _build_params: silent drop fix ---------------------------------

def test_unknown_make_raises():
    with pytest.raises(FetchError, match="not supported"):
        _adapter()._build_params(_criteria(make="Ioniq"))


def test_unknown_model_raises():
    with pytest.raises(FetchError, match="not supported"):
        _adapter()._build_params(_criteria(model="unknown"))


def test_known_make_included():
    p = _adapter()._build_params(_criteria(make="Hyundai"))
    assert p["wr_in_mnuftr_nm"] == "현대"


# --- _price_range_man: currency -------------------------------------

def test_eur_currency_raises():
    with pytest.raises(FetchError, match="currency"):
        _adapter()._price_range_man(_criteria(currency="EUR", price_min=100))


def test_price_inversion_raises():
    # Инвариант проверяется на входе (SearchCriteria), а не в адаптере.
    with pytest.raises(ValidationError, match="price_min"):
        _criteria(price_min=20000, price_max=10000)


# --- fetch: totalCnt null, rows не list ------------------------------

@pytest.mark.asyncio
async def test_fetch_total_cnt_null_does_not_crash():
    def handler(request):
        return httpx.Response(
            200, json={"data": {"rows": [], "totalCnt": None}}
        )
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    res = await a.fetch(criteria=_dummy_criteria())
    assert res.items == []


@pytest.mark.asyncio
async def test_fetch_rows_not_list_raises():
    def handler(request):
        return httpx.Response(
            200, json={"data": {"rows": {"a": 1}, "totalCnt": 0}}
        )
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    with pytest.raises(FetchError, match="rows is not a list"):
        await a.fetch(criteria=_dummy_criteria())


# --- fetch: truncated -----------------------------------------------

@pytest.mark.asyncio
async def test_fetch_truncated_logs_warning(caplog):
    def handler(request):
        return httpx.Response(
            200, json={"data": {"rows": [{"carCd": 1}], "totalCnt": 5000}}
        )
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    with caplog.at_level("WARNING"):
        res = await a.fetch(criteria=_dummy_criteria())
    assert res.source_meta["truncated"] is True
    assert "kcar_truncated" in caplog.text


# --- healthcheck ----------------------------------------------------

@pytest.mark.asyncio
async def test_healthcheck_no_data_returns_false():
    def handler(request):
        return httpx.Response(200, json={"message": "error", "data": None})
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    assert await a.healthcheck() is False


@pytest.mark.asyncio
async def test_healthcheck_empty_rows_is_healthy():
    def handler(request):
        return httpx.Response(200, json={"data": {"rows": [], "totalCnt": 0}})
    a = _adapter()
    a.http = HttpClient(transport=httpx.MockTransport(handler), timeout=5)
    assert await a.healthcheck() is True


# --- init validation -------------------------------------------------

@pytest.mark.parametrize("field", ["page_size", "price_unit", "usd_krw"])
def test_zero_config_rejected(field):
    with pytest.raises(ValueError, match=field):
        KCarApiAdapter(source_name="kcar", config={field: 0})


# --- encryption -----------------------------------------------------

def test_encrypt_params_roundtrip():
    """Хотя бы проверяем, что base64-декодируется и длина кратна 16."""
    import base64
    enc = _encrypt_params({"limit": 1})
    raw = base64.b64decode(enc)
    assert len(raw) % 16 == 0
    assert len(raw) > 0


def test_encrypt_params_non_serializable_raises():
    with pytest.raises(ValueError, match="not JSON-serializable"):
        _encrypt_params({"x": object()})