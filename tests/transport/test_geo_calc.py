"""Калькулятор с гео (GEO-V1 §2): страна отправки, маршрут и сроки.

Юнит-тесты констант `app.domain.logistics.routes` (без HTTP) и сквозные
проверки /api/calc/delivery и /api/calc/compare: сегмент страны по каждой
из KR/CN/AE/US/KZ, дефолт KR (обратная совместимость), невалидная страна
→ 422. Таможня/утильсбор считаются прежним кодом — тут не проверяются.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.logistics.routes import (
    ROUTES,
    SUPPORTED_COUNTRIES,
    route_for,
)
from app.main_api import app


@pytest.fixture
def http():
    """Клиент поверх ASGI-аппа (эндпоинты pure — без БД и auth)."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# =========================================================================
# Константы маршрутов (domain)
# =========================================================================


@pytest.mark.parametrize("country", SUPPORTED_COUNTRIES)
def test_routes_cover_supported_countries(country: str):
    """На каждую страну волны есть маршрут с портами и сроками."""
    route = ROUTES[country]
    assert route.country == country
    assert route.load_port
    assert route.discharge_port
    assert route.freight_usd >= 0
    assert 0 < route.days_min <= route.days_max


def test_route_for_is_case_insensitive():
    """route_for('kr') — как KR (нормализация регистра)."""
    assert route_for("kr").country == "KR"


def test_route_for_unknown_country_raises():
    """Неподдержанная страна → KeyError (422 на уровне схемы)."""
    with pytest.raises(KeyError):
        route_for("JP")


def test_kz_route_is_land_with_note():
    """KZ — сухопутный маршрут: фрейта нет, пометка «свой ход/автовоз»."""
    kz = ROUTES["KZ"]
    assert kz.freight_usd == 0
    assert "свой ход" in kz.route


def test_route_string_format():
    """Строка маршрута «Пусан → Владивосток» (подпись под расчётом)."""
    assert ROUTES["KR"].route == "Пусан → Владивосток"
    assert ROUTES["AE"].route == "Джебель-Али → Новороссийск"


# =========================================================================
# POST /api/calc/delivery: страна отправки
# =========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("country", "route_line", "days_min", "days_max"),
    [
        ("KR", "Пусан → Владивосток", 25, 35),
        ("CN", "Циндао → Владивосток", 20, 30),
        ("AE", "Джебель-Али → Новороссийск", 40, 55),
        ("US", "Балтимор / Лос-Анджелес → Новороссийск / Владивосток", 45, 60),
        ("KZ", "Казахстан → РФ (свой ход/автовоз)", 3, 7),
    ],
)
async def test_delivery_country_route(http, country, route_line, days_min, days_max):
    """Каждая страна волны: маршрут и окно сроков в ответе."""
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 18000, "engine_cc": 2000, "year": 2022,
              "country": country},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["country"] == country
    assert body["route"] == route_line
    assert body["freight_days_min"] == days_min
    assert body["freight_days_max"] == days_max
    # Расчёт не сломан: итоги и компоненты на месте (таможня — прежний код).
    assert body["total_usd"] > 18000
    assert len(body["components"]) == 7


@pytest.mark.asyncio
async def test_delivery_country_defaults_to_kr(http):
    """Без country — Корея, как раньше (обратная совместимость бота)."""
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 18000, "engine_cc": 2000, "year": 2022},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["country"] == "KR"
    assert body["route"] == "Пусан → Владивосток"


@pytest.mark.asyncio
async def test_delivery_country_validation_422(http):
    """country=JP (Япония вне волны) → 422, не тихий fallback на KR."""
    resp = await http.post(
        "/api/calc/delivery",
        json={"price_usd": 18000, "engine_cc": 2000, "year": 2022,
              "country": "JP"},
    )
    assert resp.status_code == 422


# =========================================================================
# POST /api/calc/compare: страна прокидывается в каждый вариант
# =========================================================================


@pytest.mark.asyncio
async def test_compare_passes_country_through(http):
    """country каждого item — в его результате (маршрут и сроки)."""
    resp = await http.post(
        "/api/calc/compare",
        json={
            "items": [
                {"price_usd": 15000, "engine_cc": 1600, "year": 2021,
                 "country": "CN", "title": "Из Китая"},
                {"price_usd": 20000, "engine_cc": 2000, "year": 2022,
                 "title": "Дефолт KR"},
            ]
        },
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    cn = next(i for i in items if i["title"] == "Из Китая")
    assert cn["country"] == "CN"
    assert cn["route"] == "Циндао → Владивосток"
    assert (cn["freight_days_min"], cn["freight_days_max"]) == (20, 30)
    kr = next(i for i in items if i["title"] == "Дефолт KR")
    assert kr["country"] == "KR"


@pytest.mark.asyncio
async def test_compare_rejects_unknown_country(http):
    """Битая страна в item → 422 на входе (как остальные поля item)."""
    resp = await http.post(
        "/api/calc/compare",
        json={"items": [{"price_usd": 9000, "year": 2018, "country": "XX"}]},
    )
    assert resp.status_code == 422
