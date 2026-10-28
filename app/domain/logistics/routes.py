"""Маршруты доставки по странам отправки (GEO-V1 §2).

Константы-приближения для калькулятора: порт загрузки, порт выгрузки,
фрейт ($) и окно сроков (дни). Цифры ориентировочные (средние по рынку
на момент волны GEO-V1, 2026-10); точная настройка — правкой констант
`ROUTES` ниже, код калькулятора при этом не меняется.

Таможня ЕАЭС (пошлина/сбор/утильсбор) здесь НЕ считается — её считает
существующий модуль `app.domain.calc.delivery` от цены/объёма/возраста.
"""

from __future__ import annotations

from dataclasses import dataclass

# Поддержанные страны отправки (сегмент калькулятора, GEO-V1 §3).
SUPPORTED_COUNTRIES: tuple[str, ...] = ("KR", "CN", "AE", "US", "KZ")


@dataclass(frozen=True, slots=True)
class DeliveryRoute:
    """Маршрут доставки из страны: порты, фрейт и окно сроков.

    Приближение для ориентира «под ключ»: фрейт — средняя ставка морской
    перевозки одного авто (настройка через константы `ROUTES`).
    """

    country: str  # ISO-3166 alpha-2 страны отправки
    load_port: str  # порт/точка загрузки
    discharge_port: str  # порт/точка выгрузки в РФ
    freight_usd: int  # средний фрейт, $
    days_min: int  # срок доставки от, дней
    days_max: int  # срок доставки до, дней
    # Пометка маршрута (сухопутные способы, варианты портов).
    note: str | None = None

    @property
    def route(self) -> str:
        """Строка маршрута «Пусан → Владивосток» (с пометкой при наличии)."""
        line = f"{self.load_port} → {self.discharge_port}"
        return f"{line} ({self.note})" if self.note else line


# Страны волны GEO-V1: Корея/Китай/ОАЭ/США морем, Казахстан сухопутно.
ROUTES: dict[str, DeliveryRoute] = {
    "KR": DeliveryRoute(
        country="KR",
        load_port="Пусан",
        discharge_port="Владивосток",
        freight_usd=1400,
        days_min=25,
        days_max=35,
    ),
    "CN": DeliveryRoute(
        country="CN",
        load_port="Циндао",
        discharge_port="Владивосток",
        freight_usd=1200,
        days_min=20,
        days_max=30,
    ),
    "AE": DeliveryRoute(
        country="AE",
        load_port="Джебель-Али",
        discharge_port="Новороссийск",
        freight_usd=2500,
        days_min=40,
        days_max=55,
    ),
    "US": DeliveryRoute(
        country="US",
        load_port="Балтимор / Лос-Анджелес",
        discharge_port="Новороссийск / Владивосток",
        freight_usd=2800,
        days_min=45,
        days_max=60,
    ),
    "KZ": DeliveryRoute(
        country="KZ",
        load_port="Казахстан",
        discharge_port="РФ",
        freight_usd=0,  # сухопутно: своего фрейта нет
        days_min=3,
        days_max=7,
        note="свой ход/автовоз",
    ),
}


def route_for(country: str) -> DeliveryRoute:
    """Маршрут страны отправки.

    Args:
        country: ISO-3166 alpha-2 (регистр не важен).

    Raises:
        KeyError: Страна не поддержана (нет в `ROUTES`).
    """
    return ROUTES[country.strip().upper()]
