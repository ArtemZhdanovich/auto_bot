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


# --- обогащение детальными карточками (окт 2026) ----------------------------

# Фрагмент реального ответа /v1/readside/vehicle/{id} (анонимно).
DETAIL_RESPONSE = {
    "vehicleId": 361895918,
    "vin": " kmhsrc81abu123456",  # мусор-регистр/пробел — нормализуется
    "spec": {"type": "CAR", "mileage": 42000, "displacement": 2199, "seatCount": 7},
    "photos": [
        {"code": "001", "path": "/carpicture09/pic3618/361895918_001.jpg", "type": "OUTER"},
        {"code": "010", "path": "/carpicture09/pic3618/361895918_010.jpg", "type": "INNER"},
    ],
    "advertisement": {
        "advertisementType": "NORMAL",
        "diagnosisCar": True,
        "directInspected": False,
        "hasUnderBodyPhoto": True,
        "oneLineText": "No,paint/TAX100%",
        "verification": {"version": "V3", "reportExists": True},
    },
    "view": {"encarDiagnosis": 1, "photoPlus": 1},
    # Комплектация (0023): коды; 0999/1001 — неизвестные, в названия не идут
    "options": {
        "type": "CAR",
        "standard": ["010", "007", "095", "087", "0999"],
        "choice": ["1001"],
        "tuning": ["023"],
        "etc": [],
    },
    "condition": {
        "accident": {"recordView": True, "resumeView": True},
        "inspection": {"formats": ["TABLE"]},
        "seizing": {"seizingCount": 0, "pledgeCount": 0},
    },
}
CLEAN_RESPONSE = {"vehicleId": 361895918, "cleaned": True}

# Чек осмотра (성능점검기록부): фрагмент реальной структуры — флаги master,
# зоны кузова (outers), чек-лист агрегатов (inners, коды групп S00-S07).
INSPECTION_RESPONSE = {
    "vehicleId": 361895918,
    "formats": ["TABLE"],
    "inspectionSource": {"code": "DIRECT_INPUT", "registrantId": "youngo06"},
    "price": None,
    "directManagement": None,
    "master": {
        "supplyNum": "2026002071",
        "accdient": False,
        "simpleRepair": True,
        "registrationDate": "2026-08-21T15:02:27",
        "detail": {
            "recordNo": "26080508106",
            "vin": "KLAZA69SDHB728319",
            "firstRegistrationDate": "20161125",
            "guarantyType": {"code": "2", "title": "보험사보증"},
        },
    },
    "outers": [
        # левая передняя дверь — покраска; бампер без дефектов → не в marks
        {"type": {"code": "O01", "title": "앞도어(좌)"}, "statusType": {"code": "6", "title": "도색"}},
        {"type": {"code": "O02", "title": "프론트 범퍼"}, "statusType": {"code": "3", "title": "없음"}},
    ],
    "inners": [
        {
            "type": {"code": "S01", "title": "원동기"},
            "children": [
                {
                    "type": {"code": "s003", "title": "작동상태(공회전)"},
                    "statusType": {"code": "1", "title": "양호"},
                    "statusItemTypes": [],
                    "children": [],
                },
                {
                    "type": {"code": "s0102", "title": "오일누유"},
                    "statusType": None,
                    "statusItemTypes": [],
                    "children": [
                        {
                            "type": {"code": "s006", "title": "실린더 블록 / 오일팬"},
                            "statusType": {"code": "7", "title": "누유"},
                            "statusItemTypes": [],
                            "children": [],
                        },
                    ],
                },
            ],
        },
        {
            "type": {"code": "S05", "title": "제동"},
            "children": [
                {
                    "type": {"code": "s032", "title": "배력장치 상태"},
                    "statusType": {"code": "10", "title": "불량"},
                    "statusItemTypes": [],
                    "children": [],
                },
            ],
        },
        # S00 (самодиагностика) не маппится в items
        {"type": {"code": "S00", "title": "자기진단"}, "children": []},
    ],
    "images": [
        {"path": "checkimg/2026/08/21/underbody1.jpg"},
        "https://ci.encar.com/checkimg/2026/08/21/room1.jpg",
        {"other": "no-path"},
    ],
    "etcs": [],
}

# Страховая история (보험이력): фрагмент реального ответа.
RECORD_RESPONSE = {
    "vehicleId": 361895918,
    "accidentCnt": 2,
    "myAccidentCnt": 0,
    "myAccidentCost": None,
    "otherAccidentCnt": 2,
    "otherAccidentCost": None,
    "floodTotalLossCnt": 0,
    "floodPartLossCnt": 1,
    "robberCnt": 0,
    "ownerChangeCnt": 5,
    "carNoChangeCnt": 1,
    "business": 0,
    "government": 0,
    "loan": 1,
    "firstDate": "2016-11-25",
    "regDate": "2026-10-23T16:45:14",
}

# 엔ка진단: зоны NORMAL/дефект + вердикты.
DIAGNOSIS_RESPONSE = {
    "vehicleId": 361895918,
    "diagnosisNo": "18",
    "diagnosisDate": "2026-09-18T00:00:00",
    "reservationCenterName": "인천 엠파크타워",
    "items": [
        {"code": "006003", "name": "FRONT_DOOR_LEFT", "result": "정상", "resultCode": "NORMAL"},
        {"code": "006009", "name": "TRUNK_LID", "result": "도색", "resultCode": "REPAINT"},
        {
            "code": "011001",
            "name": "CHECKER_COMMENT",
            "result": "본 차량은 엔카의 진단 결과 모든 항목이 정상이며 '무사고' 차량입니다.",
            "resultCode": None,
        },
        {"code": "011002", "name": "OUTER_PANEL_COMMENT", "result": "좌우쿼터패널 미세판금", "resultCode": None},
    ],
}


def make_enrich_adapter(
    detail: dict,
    clean: dict | None,
    inspection: dict | None = None,
    insp_summary: dict | None = None,
    record: dict | None = None,
    diagnosis: dict | None = None,
) -> tuple[EncarApiAdapter, list[httpx.Request]]:
    """Адаптер с маршрутизацией по URL: search → выдача, detail/clean → ответы.

    Чек/страховая/диагностика: `None` → 404 (как у авто без этих данных).
    """

    def _resp(payload: dict | None) -> httpx.Response:
        if payload is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=payload)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if "clean-encar" in url:
            if clean is None:
                return httpx.Response(500, json={})
            return httpx.Response(200, json=clean)
        if "/inspection/vehicle/" in url:
            if url.endswith("/summary"):
                return _resp(insp_summary)
            return _resp(inspection)
        if "/record/vehicle/" in url:
            return _resp(record)
        if "/diagnosis/vehicle/" in url:
            return _resp(diagnosis)
        if "readside/vehicle/" in url:
            return httpx.Response(200, json=detail)
        return httpx.Response(200, json=_payload([SAMPLE_ITEM], total=1))

    adapter = EncarApiAdapter(
        source_name="encar",
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    return adapter, requests


async def test_fetch_enriches_vin_spec_photos_inspection():
    """Detail-карточка дополняет search: VIN, объём, полный набор фото, осмотр."""
    adapter, requests = make_enrich_adapter(DETAIL_RESPONSE, CLEAN_RESPONSE)
    result = await adapter.fetch(SearchCriteria())

    item = result.items[0]
    assert item.vin == "KMHSRC81ABU123456"  # strip + upper, 17 знаков ISO 3779
    assert item.engine_volume == 2199.0  # см³ из spec.displacement
    # Полный набор фото из detail (search-набор заменён)
    assert [str(i) for i in item.images] == [
        "https://ci.encar.com/carpicture09/pic3618/361895918_001.jpg",
        "https://ci.encar.com/carpicture09/pic3618/361895918_010.jpg",
    ]
    report = item.inspection
    assert report is not None and report["source"] == "encar"
    assert "Диагностика Encar (엔카진단): пройдена" in report["summary"]
    assert "запись техосмотра (성능기록부): доступна" in report["summary"]
    assert "Clean Encar: безаварийная" in report["summary"]
    assert "отчёт верификации Encar: есть" in report["summary"]
    assert "фото днища: есть" in report["summary"]
    assert "примечание продавца: No,paint/TAX100%" in report["summary"]
    assert "осмотрена напрямую" not in report["summary"]  # directInspected=False
    assert report["grade"] is None and report["marks"] == [] and report["items"] == []
    assert report["images"] == []  # чека нет — фото неоткуда взять
    # Три запроса: search + detail + clean-encar
    urls = [str(r.url) for r in requests]
    assert any("/v1/readside/vehicle/361895918" in u for u in urls)
    assert any("/v1/readside/clean-encar/vehicle/361895918" in u for u in urls)
    await adapter.close()


async def test_fetch_extracts_options_russian():
    """Комплектация (0023): коды detail.options → RU, неизвестные коды — скип."""
    adapter, _ = make_enrich_adapter(DETAIL_RESPONSE, CLEAN_RESPONSE)
    result = await adapter.fetch(SearchCriteria())

    item = result.items[0]
    # Порядок — как у источника; 0999/1001 пропущены (сырые коды не показываем)
    assert item.options == [
        "Люк",
        "Подогрев сидений",
        "Проекционный дисплей (HUD)",
        "Круговой обзор 360°",
        "Видеорегистратор",
    ]
    await adapter.close()


async def test_fetch_builds_full_inspection_report():
    """Чек + страховая история + диагностика → marks/items/inspector/facts."""
    adapter, requests = make_enrich_adapter(
        DETAIL_RESPONSE,
        CLEAN_RESPONSE,
        inspection=INSPECTION_RESPONSE,
        insp_summary={
            "vehicleId": 361895918,
            "inspName": "엠파크오토서비스(주)타워 박기현",
            "outers": [],
            "outerSummarys": [],
        },
        record=RECORD_RESPONSE,
        diagnosis=DIAGNOSIS_RESPONSE,
    )
    result = await adapter.fetch(SearchCriteria())
    report = result.items[0].inspection
    assert report is not None

    # Кто и когда осматривал
    assert report["inspector"] == "엠파크오토서비스(주)타워 박기현"
    assert report["inspected_at"] == "2026-08-21"

    # Зоны кузова: покраска левой двери (outer) + крышки багажника (диагностика);
    # NORMAL-зоны и бампер «없음» не попадают
    assert {"zone": "door_fl", "type": "repaint", "note": "도색"} in report["marks"]
    assert {"zone": "trunk", "type": "repaint", "note": "도색"} in report["marks"]
    assert len(report["marks"]) == 2

    # Чек-лист: двигатель — подтекание масла (defect), тормоза — 불량 (defect);
    # S00 не маппится, «хороших» групп в фикстуре нет
    by_key = {i["key"]: i for i in report["items"]}
    assert by_key["engine"]["status"] == "defect"
    assert "실린더 블록 / 오일팬 — подтекание" in by_key["engine"]["note"]
    assert by_key["brakes"]["status"] == "defect"
    assert "배력장치 상태 — неисправен" in by_key["brakes"]["note"]
    assert "transmission" not in by_key  # в фикстуре группы S02/S03 пустые

    # Факты в summary
    summary = report["summary"]
    assert "чек осмотра: ДТП не отмечены" in summary
    assert "чек осмотра: простой ремонт (полировка)" in summary
    assert "гарантия страховой компании" in summary
    assert "чек заполнен продавцом/дилером, не сервисом Encar" in summary
    assert "первая регистрация: 20161125" in summary
    assert "страховые случаи: 2 (этим авто: 0, у других: 2)" in summary
    assert "частичная гибель (наводнение): 1" in summary
    assert "смен владельцев: 5" in summary
    assert "смен госномера: 1" in summary
    assert "залог/кредит в истории" in summary
    assert "вердикт Encar: 무사고 (без ДТП)" in summary
    assert "Диагностика Encar" in summary and "2026-09-18" in summary
    assert "인천 엠파크타워" in summary  # центр диагностики
    assert len(summary) <= 1000  # лимит InspectionReportOut

    # Фото чека → report["images"] (не в галерее объявления); мусор без path
    # пропущен, относительный path дополнен префиксом CDN
    assert report["images"] == [
        "https://ci.encar.com/checkimg/2026/08/21/underbody1.jpg",
        "https://ci.encar.com/checkimg/2026/08/21/room1.jpg",
    ]
    gallery = [str(u) for u in result.items[0].images]
    assert gallery == [  # detail-фото, без примеси фото чека
        "https://ci.encar.com/carpicture09/pic3618/361895918_001.jpg",
        "https://ci.encar.com/carpicture09/pic3618/361895918_010.jpg",
    ]

    # Все новые эндпоинты запрошены
    urls = [str(r.url) for r in requests]
    assert any("/v1/readside/inspection/vehicle/361895918" in u for u in urls)
    assert any(
        "/v1/readside/inspection/vehicle/361895918/summary" in u for u in urls
    )
    assert any("/v1/readside/record/vehicle/361895918/summary" in u for u in urls)
    assert any("/v1/readside/diagnosis/vehicle/361895918" in u for u in urls)
    await adapter.close()


async def test_fetch_report_without_extra_apis():
    """404 чека/страховой/диагностики → отчёт из флагов, marks/items пусты."""
    adapter, _ = make_enrich_adapter(DETAIL_RESPONSE, CLEAN_RESPONSE)
    result = await adapter.fetch(SearchCriteria())
    report = result.items[0].inspection
    assert report is not None
    assert report["marks"] == [] and report["items"] == []
    assert report["inspector"] is None and report["inspected_at"] is None
    assert report["images"] == []
    assert "Диагностика Encar (엔카진단): пройдена" in report["summary"]
    assert "страховые случаи" not in report["summary"]
    await adapter.close()


async def test_fetch_survives_detail_failure():
    """«Мусорный» detail (без vin/spec/photos) не портит search-данные."""
    adapter, _ = make_enrich_adapter({"message": "Not Found"}, None)  # clean → 500
    result = await adapter.fetch(SearchCriteria())
    item = result.items[0]
    assert item.vin is None  # в detail нет валидного VIN
    assert [str(i) for i in item.images] == [  # search-фото не потеряны
        "https://ci.encar.com/photo/nc1/2024/01/1.jpg",
        "https://ci.encar.com/photo/nc1/2024/01/2.jpg",
    ]
    assert len(result.items) == 1
    await adapter.close()


async def test_bad_vin_not_saved():
    """Мусор вместо VIN (госномер/короткая строка) не попадает в listing."""
    bad = dict(DETAIL_RESPONSE, vin="812루7127")
    adapter, _ = make_enrich_adapter(bad, CLEAN_RESPONSE)
    result = await adapter.fetch(SearchCriteria())
    assert result.items[0].vin is None
    await adapter.close()



async def test_fetch_maps_to_unified_listing():
    adapter, requests = make_adapter([_payload([SAMPLE_ITEM], total=1)])
    result = await adapter.fetch(SearchCriteria())

    # search + detail-карточка + clean-encar (обогащение окт 2026)
    assert len(requests) == 7  # search + detail + clean + чек + его summary + страховая + диагностика
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
