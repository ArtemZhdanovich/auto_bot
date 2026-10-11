"""Контрактные тесты EncarApiAdapter (этап 2: мок ответа → UnifiedListing)."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters.common.http import HttpClient
from app.adapters.encar import EncarApiAdapter
from app.schemas.criteria import SearchCriteria

SAMPLE_ITEM = {
    "Id": "361895918",
    "Subject": "팰리세이드 2.2 4WD 캘리그래피",
    "Manufacturer": "Hyundai",
    "Model": "Palisade",
    "Badge": "Caligraphy",
    "Year": 2022,
    "Price": 3850,  # ман (x10000 = KRW)
    "Mileage": 42000,
    "FuelType": "디젤",
    "Transmission": "오토",
    "Color": "흰색",
    "OfficeCity": "부산",
    "Photos": ["photo/nc1/2024/01/1.jpg", "/photo/nc1/2024/01/2.jpg"],
}


def _payload(items: list[dict], total: int | None = None) -> dict:
    return {"Count": total if total is not None else len(items), "SearchResults": items}


def make_adapter(responses: list[dict]) -> tuple[EncarApiAdapter, list[httpx.Request]]:
    """Адаптер с MockTransport: отдаёт responses по очереди на каждый запрос."""
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = queue.pop(0) if queue else _payload([])
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    adapter = EncarApiAdapter(
        source_name="encar",
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
    assert item.source == "encar"
    assert item.source_id == "361895918"
    assert str(item.url) == "https://fem.encar.com/car/detail/361895918"
    assert item.make == "Hyundai"
    assert item.model == "Palisade"
    assert item.generation == "Caligraphy"
    assert item.year == 2022
    assert item.price == 38_500_000  # ман → KRW
    assert item.currency == "KRW"
    assert item.mileage_km == 42000
    assert item.fuel == "diesel"  # 디젤 → diesel
    assert item.transmission == "automatic"  # 오토 → automatic
    assert item.location == "부산"
    assert item.seller_type == "dealer"
    assert [str(i) for i in item.images] == [
        "https://ci.encar.com/photo/nc1/2024/01/1.jpg",
        "https://ci.encar.com/photo/nc1/2024/01/2.jpg",
    ]
    assert item.raw == SAMPLE_ITEM  # raw_payload сохраняется (ТЗ п. 6.1)
    await adapter.close()


async def test_fetch_skips_items_without_id():
    broken = dict(SAMPLE_ITEM)
    del broken["Id"]
    adapter, _ = make_adapter([_payload([broken, SAMPLE_ITEM])])
    result = await adapter.fetch(SearchCriteria())
    assert len(result.items) == 1
    await adapter.close()


async def test_fetch_pagination_cursor():
    adapter, _ = make_adapter([_payload([SAMPLE_ITEM], total=40), _payload([], total=40)])
    first = await adapter.fetch(SearchCriteria(), cursor=None)
    assert first.has_more is True
    assert first.next_cursor == "1"

    second = await adapter.fetch(SearchCriteria(), cursor=first.next_cursor)
    assert second.has_more is False
    assert second.next_cursor is None
    await adapter.close()


async def test_build_q_filters():
    adapter, requests = make_adapter([])
    criteria = SearchCriteria(
        make="현대", model="Palisade", year_from=2022, year_to=2024,
        price_max=45000, mileage_max=50000, fuel="diesel",
    )
    await adapter.fetch(criteria)

    q = parse_qs(urlparse(str(requests[0].url)).query)["q"][0]
    assert q.startswith("(And.Hidden.N.)")
    assert "(And Make.Hyundai. Model.Palisade.)" in q
    assert "(And.Price.0.45000.)" in q
    assert "(And.Year.2022.2024.)" in q
    assert "(And.Mileage.0.50000.)" in q
    assert "(And.FuelType.diesel.)" in q
    await adapter.close()


async def test_healthcheck_true_on_200():
    adapter, _ = make_adapter([_payload([])])
    assert await adapter.healthcheck() is True
    await adapter.close()


async def test_healthcheck_false_on_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    adapter = EncarApiAdapter(
        source_name="encar",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    assert await adapter.healthcheck() is False
    await adapter.close()


async def test_fetch_raises_fetch_error_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    adapter = EncarApiAdapter(
        source_name="encar",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    from app.adapters.common.http import FetchError

    with pytest.raises(FetchError):
        await adapter.fetch(SearchCriteria())
    await adapter.close()
