"""API калькулятора доставки (MVP): POST /api/calc/delivery и /api/calc/compare."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def http():
    """Клиент поверх ASGI-аппа (эндпоинты pure — без БД и auth)."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_delivery_200_full_breakdown(http):
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 18000, "engine_cc": 2000, "year": 2022},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["price_usd"] == 18000
    assert body["age_years"] >= 3
    assert body["age_group"] in ("3–5 лет", "старше 5 лет")
    # 7 компонентов: авто, море, брокер, СБКТС/ЭПТС, пошлина, сбор, утильсбор
    assert len(body["components"]) == 7
    labels = {c["label"] for c in body["components"]}
    assert any("пошлина" in lb.lower() for lb in labels)
    assert any("утиль" in lb.lower() for lb in labels)
    # пошлина 3–5 лет, 1800–2300 см³: 2.7 €/см³ × 2000 × 1.08 = 5832 $
    duty = next(c for c in body["components"] if "пошлина" in c["label"].lower())
    assert duty["usd"] == 5832
    # утильсбор льготный (мощность не указана → ≤160 л.с. в предположении)
    assert body["util_category"] == "льготный"
    assert body["util_note"]
    util = next(c for c in body["components"] if "утиль" in c["label"].lower())
    assert util["rub"] == 5_200
    # итог дороже авто и согласован ₽/$
    assert body["total_usd"] > 18000
    assert body["total_rub"] == pytest.approx(body["total_usd"] * 92.0, rel=0.01)


@pytest.mark.asyncio
async def test_delivery_power_over_160_full_util(http):
    """Мощность >160 л.с. → льгота утильсбора не применяется (ПП № 1291)."""
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 30000, "engine_cc": 2500, "year": 2024, "power_hp": 235},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["power_hp"] == 235
    assert body["util_category"] == "полный"
    assert body["util_note"] == ""
    util = next(c for c in body["components"] if "утиль" in c["label"].lower())
    assert util["rub"] == 20_000 * 120.12  # коэф. 220–250 л.с. (2,0–3,0 л), до 3 лет


@pytest.mark.asyncio
async def test_delivery_defaults_cc_2000(http):
    resp = await http.post(
        "/api/calc/delivery", json={"price_usd": 10000, "year": 2020}
    )
    assert resp.status_code == 200
    assert resp.json()["engine_cc"] == 2000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"price_usd": -5, "year": 2022},          # цена ≤ 0
        {"price_usd": 100, "year": 1979},          # год < 1980
        {"price_usd": 100, "year": 2022, "engine_cc": 50_000},  # см³ > 10 000
        {"year": 2022},                            # нет цены
    ],
)
async def test_delivery_validation_422(http, payload):
    resp = await http.post("/api/calc/delivery", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_compare_sorted_by_total(http):
    resp = await http.post(
        "/api/calc/compare",
        json={
            "items": [
                {"price_usd": 18000, "engine_cc": 2000, "year": 2022, "title": "Palisade"},
                {"price_usd": 15000, "engine_cc": 1600, "year": 2021, "title": "Sportage"},
            ]
        },
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["title"] for i in items] == ["Sportage", "Palisade"]  # дешевый первым
    assert items[0]["total_usd"] <= items[1]["total_usd"]
    for i in items:
        # ₽/$ согласованы (допуск ±1 ₽ на округление independent-раундов)
        assert abs(i["total_rub"] - i["total_usd"] * 92) < 92
        assert i["age_group"]


@pytest.mark.asyncio
async def test_compare_skips_bad_items_and_falls_back_title(http):
    resp = await http.post(
        "/api/calc/compare",
        json={
            "items": [
                {"engine_cc": 2000, "year": 2022},  # нет цены → пропустить
                {"price_usd": 9000, "year": 2018},  # без title → автотайтл
            ]
        },
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "2018 г. · 2000 см³"


@pytest.mark.asyncio
async def test_compare_empty_items_422(http):
    resp = await http.post("/api/calc/compare", json={"items": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delivery_response_includes_legal_refs(http):
    """UI показывает правовые основания расчёта (НПА), а не голые цифры."""
    resp = await http.post(
        "/api/calc/delivery", json={"price_usd": 18000, "engine_cc": 2000, "year": 2022}
    )
    assert resp.status_code == 200
    refs = resp.json()["legal_refs"]
    assert len(refs) >= 3
    joined = " ".join(r["ref"] for r in refs)
    assert "ЕТТ ЕАЭС" in joined and "1291" in joined and "1637" in joined


@pytest.mark.asyncio
async def test_delivery_power_validation_422(http):
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 18000, "year": 2022, "power_hp": 5},
    )
    assert resp.status_code == 422


# ---------- Регион «by»: валюта объявления BYN → юрисдикция РБ ----------


@pytest.mark.asyncio
async def test_delivery_by_region_local_currency(http):
    """region="by": итог в BYN, сбор/утильсбор и маршрут — по законодательству РБ."""
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 18000, "engine_cc": 2000, "year": 2022, "region": "by"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["region"] == "by"
    assert body["currency"] == "Br"
    # льготный утильсбор РБ: 544,5 × 0.5 = 272,25 BYN (объём ≤ 3 000 см³)
    util = next(c for c in body["components"] if "утиль" in c["label"].lower())
    assert "РБ" in util["label"]
    assert util["local"] == 272  # round(544,5 × 0.5 = 272,25)
    # сбор за оформление РБ: 48 BYN (не шкала ПП № 1637)
    fee = next(c for c in body["components"] if "оформление" in c["label"].lower())
    assert fee["local"] == 48
    # итог локальный согласован с USD-эквивалентом (курс 3.05)
    assert body["total_usd"] == pytest.approx(body["total_local"] / 3.05, rel=0.01)
    assert body["total_local"] > 0
    # местные суммы в каждом компоненте заполнены
    assert all(c["local"] > 0 for c in body["components"])
    # утильсбор РБ заметно меньше РФ-льготного (5 200 ₽ ≈ 155 Br)
    assert util["local"] < 400


@pytest.mark.asyncio
async def test_delivery_by_region_legal_refs(http):
    """Для РБ — ссылки на НПА РБ, а не только ПП РФ."""
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 18000, "engine_cc": 2000, "year": 2022, "region": "by"},
    )
    refs = resp.json()["legal_refs"]
    joined = " ".join(r["ref"] for r in refs)
    assert "ЕТТ ЕАЭС" in joined  # пошлины ЕАЭС общие
    assert "Совмина РБ" in joined  # сбор + утильсбор РБ
    assert "customs.gov.by" in joined  # пометка «проверять»


@pytest.mark.asyncio
async def test_delivery_by_region_full_util_over_3000cc(http):
    """Объём > 3 000 см³ в РБ → без льготы (полный утильсбор, ориентир)."""
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 30000, "engine_cc": 3400, "year": 2022, "region": "by"},
    )
    body = resp.json()
    assert body["util_category"] == "полный"
    util = next(c for c in body["components"] if "утиль" in c["label"].lower())
    assert util["local"] == 1_089.0  # 544,5 × 2.0


@pytest.mark.asyncio
async def test_delivery_default_region_is_ru(http):
    """Без region — РФ как раньше (обратная совместимость бота/агента)."""
    resp = await http.post(
        "/api/calc/delivery", json={"price_usd": 18000, "engine_cc": 2000, "year": 2022}
    )
    body = resp.json()
    assert body["region"] == "ru"
    assert body["currency"] == "₽"
    assert body["total_local"] == body["total_rub"]


@pytest.mark.asyncio
async def test_delivery_region_validation_422(http):
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 18000, "year": 2022, "region": "kz"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_compare_region_passthrough(http):
    """compare: region прокидывается в расчёт каждого варианта (BYN → Br)."""
    resp = await http.post(
        "/api/calc/compare",
        json={
            "items": [
                {"price_usd": 15000, "engine_cc": 1600, "year": 2021, "region": "by"},
                {"price_usd": 20000, "engine_cc": 2000, "year": 2022},
            ]
        },
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    by_item = next(i for i in items if i["currency"] == "Br")
    assert by_item["total_local"] > 0
    ru_item = next(i for i in items if i["currency"] == "₽")
    assert ru_item["total_local"] == ru_item["total_rub"]
