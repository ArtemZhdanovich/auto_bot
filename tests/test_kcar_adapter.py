"""Контрактные тесты KCarApiAdapter (шифрованный POST /bc/search/list)."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.adapters.common.http import FetchError, HttpClient
from app.adapters.common.normalize import normalize_make
from app.adapters.kcar import KCAR_MAKE_NAMES, KCarApiAdapter, _encrypt_params
from app.schemas.criteria import SearchCriteria

SAMPLE_ITEM = {
    "carCd": "EC61405190",
    "carWhlNm": "현대 제네시스 BH330 럭셔리 기본형",
    "mnuftrNm": "현대",
    "modelNm": "제네시스",
    "grdNm": "BH330 럭셔리",
    "grdDtlNm": "기본형",
    "prdcnYr": "2011",
    "prc": "630",  # ман (x10000 = KRW)
    "milg": "88856",
    "fuelNm": "가솔린",
    "trnsmsnNm": "오토",
    "extrColorNm": "은회색",
    "carctgrNm": "대형차",
    "cntrRgnNm": "경기/인천",
    "lsizeImgPath": "https://img.kcar.com/carpicture/carpicture10/pic6140/kcarM_61405190_001.jpg",
}


def _payload(items: list[dict], total: int | None = None) -> dict:
    return {
        "message": None,
        "data": {"totalCnt": total if total is not None else len(items), "rows": items},
    }


def _decrypt_params(enc: str) -> dict:
    """Обратная операция _encrypt_params (для проверки тела запроса)."""
    decryptor = Cipher(
        algorithms.AES(b"SKFJ2424DasfaJRI"), modes.CBC(b"sfq241sf3dscs321")
    ).decryptor()
    padded = decryptor.update(base64.b64decode(enc)) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return json.loads(unpadder.update(padded) + unpadder.finalize())


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


def test_encrypt_params_roundtrip():
    """enc совместим с фронтом: base64 от AES-CBC(key/iv setEncDef), PKCS7."""
    enc = _encrypt_params({"limit": 3})
    raw = base64.b64decode(enc)
    assert len(raw) % 16 == 0
    assert _decrypt_params(enc) == {"limit": 3}


async def test_fetch_maps_to_unified_listing():
    adapter, requests = make_adapter([_payload([SAMPLE_ITEM], total=8857)])
    result = await adapter.fetch(SearchCriteria())

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "https://api.kcar.com/bc/search/list"
    assert _decrypt_params(json.loads(requests[0].content)["enc"])["limit"] == 100

    assert len(result.items) == 1
    item = result.items[0]
    assert item.source == "kcar"
    assert item.source_id == "EC61405190"
    assert str(item.url) == "https://www.kcar.com/bc/detail/CarInfoDtl?carCd=EC61405190"
    assert item.make == "Hyundai"  # 현대 → Hyundai
    assert item.model == "제네시스"
    assert item.generation == "BH330 럭셔리 기본형"  # grdNm + grdDtlNm
    assert item.title == "현대 제네시스 BH330 럭셔리 기본형"  # carWhlNm
    assert item.year == 2011  # prdcnYr
    assert item.price == 6_300_000  # 630 ман → KRW
    assert item.currency == "KRW"
    assert item.mileage_km == 88856  # milg
    assert item.fuel == "petrol"  # 가솔린 → petrol
    assert item.transmission == "automatic"  # 오토 → automatic
    assert item.color == "은회색"  # extrColorNm
    assert item.location == "경기/인천"  # cntrRgnNm
    assert item.seller_type == "dealer"  # kcar — сеть 직영-центров
    assert [str(i) for i in item.images] == [SAMPLE_ITEM["lsizeImgPath"]]
    assert item.raw == SAMPLE_ITEM  # raw_payload сохраняется (ТЗ п. 6.1)
    assert result.source_meta == {"total": 8857, "fetched": 1}
    await adapter.close()


async def test_fetch_skips_items_without_id():
    broken = dict(SAMPLE_ITEM)
    del broken["carCd"]
    adapter, _ = make_adapter([_payload([broken, SAMPLE_ITEM])])
    result = await adapter.fetch(SearchCriteria())
    assert len(result.items) == 1
    await adapter.close()


async def test_fetch_no_pagination_cursor():
    """Пагинации в API нет: одна страница, has_more=False, cursor=None."""
    adapter, _ = make_adapter([_payload([SAMPLE_ITEM], total=8857)])
    result = await adapter.fetch(SearchCriteria(), cursor="2")  # cursor игнорируется
    assert result.has_more is False
    assert result.next_cursor is None
    await adapter.close()


async def test_build_params_filters():
    adapter, requests = make_adapter([])
    criteria = SearchCriteria(
        make="Hyundai", model="Grandeur", year_from=2021, year_to=2023,
        price_min=10_000_000, price_max=45_000_000, mileage_max=60000,
        fuel="petrol",
    )
    await adapter.fetch(criteria)

    params = _decrypt_params(json.loads(requests[0].content)["enc"])
    assert params["wr_in_mnuftr_nm"] == "현대"
    assert params["wr_in_model_nm"] == "그랜저"
    assert params["wr_gt_mfg_dt"] == "202101"  # год → YYYYMM
    assert params["wr_lt_mfg_dt"] == "202312"
    assert params["wr_gt_prc"] == "1000"  # KRW → ман
    assert params["wr_lt_prc"] == "4500"
    assert params["wr_lt_milg"] == "60000"
    assert params["wr_in_fuel_cd"] == "001"  # petrol (подтверждённый код)
    await adapter.close()


async def test_build_params_unmapped_filters_skipped():
    """Непереводимые марка/модель/топливо не превращаются в пустые фильтры."""
    adapter, requests = make_adapter([])
    await adapter.fetch(SearchCriteria(make="ZAZ", model="Tavria", fuel="hybrid"))
    params = _decrypt_params(json.loads(requests[0].content)["enc"])
    assert "wr_in_mnuftr_nm" not in params
    assert "wr_in_model_nm" not in params
    assert "wr_in_fuel_cd" not in params  # код не подтверждён — не шлём
    await adapter.close()


def test_make_names_consistent_with_normalize():
    """Карта EN→KR согласована: normalize_make(KR) возвращает обратно EN."""
    for en_name, kr_name in KCAR_MAKE_NAMES.items():
        assert normalize_make(kr_name) == en_name, f"{kr_name} -> {en_name}"


async def test_price_unit_from_config():
    adapter, _ = make_adapter([_payload([SAMPLE_ITEM])])
    adapter._price_unit = 1  # источник уже отдаёт полную цену
    result = await adapter.fetch(SearchCriteria())
    assert result.items[0].price == 630
    await adapter.close()


async def test_healthcheck_true_on_200():
    adapter, requests = make_adapter([_payload([], total=0)])
    assert await adapter.healthcheck() is True
    assert _decrypt_params(json.loads(requests[0].content)["enc"]) == {"limit": 1}
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
