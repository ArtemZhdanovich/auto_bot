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
    # итог дороже авто и согласован ₽/$
    assert body["total_usd"] > 18000
    assert body["total_rub"] == pytest.approx(body["total_usd"] * 92.0, rel=0.01)


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
    assert "ЕТТ ЕАЭС" in joined and "1291" in joined and "342" in joined
