"""Контрактные тесты EncarApiAdapter (этап 2: мок ответа → UnifiedListing).

Проверяется контракт окт 2026: sr/inav/cursor обязательны, q — новый синтаксис
с разделителем `_.` и корейскими значениями фасетов.
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.infrastructure.sources.common.http import FetchError, HttpClient
from app.infrastructure.sources.encar import EncarApiAdapter
from app.domain.search.criteria import SearchCriteria

SAMPLE_ITEM = {
    "Id": "361895918",
    "Manufacturer": "현대",
    "Model": "팰리세이드",
    "Badge": "Caligraphy",
    "FormYear": "2022",
    "Year": 202208.0,
    "Price": 3850,  # ман (x10000 = KRW)
    "Mileage": 42000,
    "FuelType": "디젤",
    "OfficeCityState": "부산",
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


def _params_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(request.url)).query, keep_blank_values=True)


async def test_fetch_maps_to_unified_listing():
    adapter, requests = make_adapter([_payload([SAMPLE_ITEM], total=1)])
    result = await adapter.fetch(SearchCriteria())

    assert len(requests) == 1
    assert len(result.items) == 1
    item = result.items[0]
    assert item.source == "encar"
    assert item.source_id == "361895918"
    assert str(item.url) == "https://fem.encar.com/car/detail/361895918"
    assert item.make == "Hyundai"  # 현대 → Hyundai
    assert item.model == "팰리세이드"
    assert item.title == "현대 팰리세이드"  # Subject больше нет в списке
    assert item.generation == "Caligraphy"
    assert item.year == 2022  # FormYear '2022' (Year 202208.0 → тоже 2022)
    assert item.price == 38_500_000  # ман → KRW
    assert item.currency == "KRW"
    assert item.mileage_km == 42000
    assert item.fuel == "diesel"  # 디젤 → diesel
    assert item.location == "부산"  # OfficeCityState
    assert item.seller_type == "dealer"
    assert [str(i) for i in item.images] == [
        "https://ci.encar.com/photo/nc1/2024/01/1.jpg",
        "https://ci.encar.com/photo/nc1/2024/01/2.jpg",
    ]
    assert item.raw == SAMPLE_ITEM  # raw_payload сохраняется (ТЗ п. 6.1)
    await adapter.close()


async def test_request_has_new_contract_params():
    """sr/inav/cursor обязательны: без sr API отдаёт пустой SearchResults."""
    adapter, requests = make_adapter([_payload([SAMPLE_ITEM], total=1)])
    await adapter.fetch(SearchCriteria(), cursor="2")

    params = _params_of(requests[0])
    assert params["count"] == ["true"]
    assert params["sr"] == ["|ModifiedDate|40|20"]  # страница 2 → offset 40
    assert params["inav"] == ["|Metadata|Sort"]
    assert params["cursor"] == [""]
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


async def test_build_q_filters_new_syntax():
    adapter, requests = make_adapter([])
    criteria = SearchCriteria(
        make="현대", model="Palisade", year_from=2022, year_to=2024,
        price_max=45_000_000, mileage_max=50000, fuel="diesel",
    )
    await adapter.fetch(criteria)

    q = _params_of(requests[0])["q"][0]
    # Новый синтаксис: одно (And …), разделитель `_.`, корейские фасеты.
    assert q == (
        "(And.Hidden.N"
        "._.Manufacturer.현대"
        "._.ModelGroup.팰리세이드"
        "._.Price.range(0..4500)"  # 45 млн KRW → 4500 ман
        "._.Year.range(202201..202412)"
        "._.Mileage.range(0..50000)"
        "._.FuelType.디젤.)"
    )
    await adapter.close()


async def test_build_q_usd_currency_converted():
    adapter, requests = make_adapter([])
    criteria = SearchCriteria(price_min=30000, price_max=45000, currency="USD")
    await adapter.fetch(criteria)

    q = _params_of(requests[0])["q"][0]
    # 30000$ * 1400 = 42 млн KRW → 4200 ман; 45000$ → 6300 ман
    assert "Price.range(4200..6300)" in q
    await adapter.close()


async def test_build_q_rejects_unknown_make():
    """Неизвестная марка — явная ошибка, а не запрос без фильтра.

    Молчаливый сброс вернул бы машины других марок; лучше не отдавать
    пользователю неверную выдачу.
    """
    adapter, requests = make_adapter([])
    with pytest.raises(FetchError, match="not supported"):
        await adapter.fetch(SearchCriteria(make="Lada", model="Vesta"))
    assert requests == []
    await adapter.close()


async def test_healthcheck_true_on_count_and_results():
    adapter, _ = make_adapter([_payload([SAMPLE_ITEM], total=192000)])
    assert await adapter.healthcheck() is True
    await adapter.close()


async def test_healthcheck_true_on_count_only():
    # Контракт «только счётчик»: Count есть, ключа SearchResults нет.
    adapter, _ = make_adapter([{"Count": 192000}])
    assert await adapter.healthcheck() is True
    await adapter.close()


async def test_healthcheck_false_on_empty_payload():
    adapter, _ = make_adapter([{}])
    assert await adapter.healthcheck() is False
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
    from app.infrastructure.sources.common.http import FetchError

    with pytest.raises(FetchError):
        await adapter.fetch(SearchCriteria())
    await adapter.close()
