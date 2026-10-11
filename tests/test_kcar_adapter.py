"""Контрактные тесты KCarApiAdapter (этап 7: мок ответа → UnifiedListing)."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters.common.http import FetchError, HttpClient
from app.adapters.kcar import KCarApiAdapter
from app.schemas.criteria import SearchCriteria

SAMPLE_ITEM = {
    "carSeq": "22334455",
    "carName": "그랜저 IG 2.5 가솔린 프리미엄",
    "brandName": "Hyundai",
    "modelName": "Grandeur",
    "gradeName": "Premium",
    "year": 2021,
    "sellingPrice": 2730,  # ман (x10000 = KRW)
    "mileage": 51000,
    "fuelName": "가솔린",
    "gearName": "오토",
    "colorName": "흰색",
    "addr": "서울",
    "photoList": ["photo/kc/2024/02/1.jpg", "/photo/kc/2024/02/2.jpg"],
}


def _payload(items: list[dict], total: int | None = None) -> dict:
    return {"totalCnt": total if total is not None else len(items), "list": items}


def make_adapter(responses: list[dict]) -> tuple[KCarApiAdapter, list[httpx.Request]]:
    """Адаптер с MockTransport: отдаёт responses по очереди на каждый запрос."""
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = queue.pop(0) if queue else _payload([])
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    adapter = KCarApiAdapter(
        source_name="kcar",
        http_client=HttpClient(transport=transport, timeout=10),
    )
    adapter.requests = requests  # type: ignore[attr-defined]
    return adapter, requests


async def test_fetch_maps_to_unified_listing():
    adapter, requests = make_adapter([_payload([SAMPLE_ITEM], total=1)])
    result = await adapter.fetch(SearchCriteria())

    assert len(requests) == 1
    assert len(result.items) == 1
    item = result.items[0]
    assert item.source == "kcar"
    assert item.source_id == "22334455"
    assert (
        str(item.url)
        == "https://www.kcar.com/rebuilt/car/info/carView.do?carSeq=22334455"
    )
    assert item.make == "Hyundai"
    assert item.model == "Grandeur"
    assert item.generation == "Premium"
    assert item.year == 2021
    assert item.price == 27_300_000  # ман → KRW
    assert item.currency == "KRW"
    assert item.mileage_km == 51000
    assert item.fuel == "petrol"  # 가솔린 → petrol
    assert item.transmission == "automatic"  # 오토 → automatic
    assert item.location == "서울"
    assert item.seller_type == "dealer"
    assert [str(i) for i in item.images] == [
        "https://ci.kcar.com/photo/kc/2024/02/1.jpg",
        "https://ci.kcar.com/photo/kc/2024/02/2.jpg",
    ]
    assert item.raw == SAMPLE_ITEM  # raw_payload сохраняется (ТЗ п. 6.1)
    await adapter.close()


async def test_fetch_skips_items_without_id():
    broken = dict(SAMPLE_ITEM)
    del broken["carSeq"]
    adapter, _ = make_adapter([_payload([broken, SAMPLE_ITEM])])
    result = await adapter.fetch(SearchCriteria())
    assert len(result.items) == 1
    await adapter.close()


async def test_fetch_pagination_cursor_starts_at_1():
    adapter, _ = make_adapter([_payload([SAMPLE_ITEM], total=40), _payload([], total=40)])
    first = await adapter.fetch(SearchCriteria(), cursor=None)
    assert first.has_more is True
    assert first.next_cursor == "2"  # KCar: страницы с 1

    second = await adapter.fetch(SearchCriteria(), cursor=first.next_cursor)
    assert second.has_more is False
    assert second.next_cursor is None
    await adapter.close()


async def test_build_params_filters():
    adapter, requests = make_adapter([])
    criteria = SearchCriteria(
        make="현대", model="Grandeur", year_from=2021, year_to=2023,
        price_max=45000, mileage_max=60000, fuel="petrol",
    )
    await adapter.fetch(criteria)

    qs = parse_qs(urlparse(str(requests[0].url)).query)
    assert qs["brandName"] == ["Hyundai"]
    assert qs["modelName"] == ["Grandeur"]
    assert qs["yearFrom"] == ["2021"]
    assert qs["yearTo"] == ["2023"]
    assert qs["priceTo"] == ["45000"]
    assert qs["mileageTo"] == ["60000"]
    assert qs["fuel"] == ["petrol"]
    await adapter.close()


async def test_price_unit_from_config():
    adapter, _ = make_adapter([_payload([SAMPLE_ITEM])])
    adapter._price_unit = 1  # источник уже отдаёт полную цену
    result = await adapter.fetch(SearchCriteria())
    assert result.items[0].price == 2730
    await adapter.close()


async def test_healthcheck_true_on_200():
    adapter, _ = make_adapter([_payload([])])
    assert await adapter.healthcheck() is True
    await adapter.close()


async def test_healthcheck_false_on_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    adapter = KCarApiAdapter(
        source_name="kcar",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    assert await adapter.healthcheck() is False
    await adapter.close()


async def test_fetch_raises_fetch_error_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    adapter = KCarApiAdapter(
        source_name="kcar",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    with pytest.raises(FetchError):
        await adapter.fetch(SearchCriteria())
    await adapter.close()
