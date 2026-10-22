"""Калькулятор доставки Корея → РФ/РБ (MVP: сравнение цен «под ключ»).

`/delivery` — один расчёт с полной валидацией (`DeliveryIn`).
`/compare` — сравнение до 10 вариантов; каждый item валидируется
Pydantic-моделью `CompareItem` (как `DeliveryIn` + `title`), битый
item даёт 422, а не молча выпадает из результата.

`region` в обоих эндпоинтах — `Literal["ru", "by"]`; любое другое
значение → 422. Это защита от тихого применения RU-правил к
не-RU региону.

`year` ограничен `le=2100` (контракт), но в `calc_delivery` при
`year > current_year + 1` пишется warning: возраст будет
отрицательным, и ставка утильсбора может быть некорректной.
"""

from app.transport.http.deps.dishka import DishkaRoute

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.calc.use_cases import DeliveryBreakdown, calc_delivery

logger = logging.getLogger(__name__)

router = APIRouter(route_class=DishkaRoute, prefix="/api/calc", tags=["calc"])


class DeliveryIn(BaseModel):
    """Запрос на расчёт «под ключ» для одного авто."""

    price_usd: float = Field(gt=0, le=1_000_000)
    engine_cc: int = Field(default=2000, ge=50, le=10_000)
    year: int = Field(ge=1980, le=2100)
    power_hp: int | None = Field(default=None, ge=10, le=2000)
    # Юрисдикция расчёта (валюта объявления: BYN → РБ, RUB → РФ)
    region: Literal["ru", "by"] = "ru"


class DeliveryOut(BaseModel):
    """Результат расчёта «под ключ»."""

    price_usd: float
    engine_cc: int
    year: int
    age_years: int
    age_group: str
    region: str = "ru"
    currency: str = "₽"
    power_hp: int | None = None
    util_category: str = "льготный"
    util_note: str = ""
    components: list[dict[str, Any]] = Field(default_factory=list)
    legal_refs: list[dict[str, Any]] = Field(default_factory=list)
    total_rub: float
    total_usd: float
    total_local: float
    rates_note: str = (
        "Курс фиксированный (MVP). Логистика/брокер/СБКТС — средние по рынку."
    )


def _warn_future_year(year: int) -> None:
    """Логирует warning, если год выпуска в будущем (возраст отрицательный).

    Args:
        year: Год выпуска.
    """
    current_year = datetime.now(timezone.utc).year
    if year > current_year + 1:
        logger.warning(
            "calc_future_year year=%d current=%d (age will be negative)",
            year, current_year,
        )


@router.post("/delivery", response_model=DeliveryOut)
async def calc_delivery_endpoint(payload: DeliveryIn) -> DeliveryOut:
    """Расчёт «авто + море + брокер + СБКТС/ЭПТС + пошлина + сборы + утиль» (region: ru|by).

    Args:
        payload: Параметры авто.

    Returns:
        `DeliveryOut` с компонентами и итогами.

    Raises:
        HTTPException: 400 если `calc_delivery` не может обработать
            параметры (невалидный регион в сервисе, год вне таблицы и т.п.).
    """
    _warn_future_year(payload.year)
    try:
        b: DeliveryBreakdown = calc_delivery(
            payload.price_usd,
            payload.engine_cc,
            payload.year,
            region=payload.region,
            power_hp=payload.power_hp,
        )
    except (ValueError, KeyError) as exc:
        logger.warning(
            "calc_delivery_failed price=%s cc=%s year=%s region=%s: %s",
            payload.price_usd, payload.engine_cc, payload.year,
            payload.region, exc,
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Не удалось рассчитать доставку: {exc}",
        ) from exc
    return DeliveryOut(
        price_usd=b.price_usd,
        engine_cc=b.engine_cc,
        year=b.year,
        age_years=b.age_years,
        age_group=b.age_group,
        region=b.region,
        currency=b.currency,
        power_hp=b.power_hp,
        util_category=b.util_category,
        util_note=b.util_note,
        components=b.components,
        legal_refs=b.legal_refs,
        total_rub=b.total_rub,
        total_usd=b.total_usd,
        total_local=b.total_local,
    )


class CompareItem(BaseModel):
    """Один вариант для сравнения (как `DeliveryIn` + `title`)."""

    title: str | None = Field(default=None, max_length=200)
    price_usd: float = Field(gt=0, le=1_000_000)
    engine_cc: int = Field(default=2000, ge=50, le=10_000)
    year: int = Field(ge=1980, le=2100)
    power_hp: int | None = Field(default=None, ge=10, le=2000)
    region: Literal["ru", "by"] = "ru"


class CompareIn(BaseModel):
    """Сравнение нескольких вариантов «под ключ»."""

    items: list[CompareItem] = Field(min_length=1, max_length=10)


class CompareItemOut(BaseModel):
    """Один результат сравнения."""

    title: str
    price_usd: float
    year: int
    engine_cc: int
    power_hp: int | None = None
    region: str
    currency: str
    age_group: str
    total_usd: float
    total_rub: float
    total_local: float


class CompareOut(BaseModel):
    """Результат сравнения, отсортированный по итоговой цене."""

    items: list[CompareItemOut]


@router.post("/compare", response_model=CompareOut)
async def calc_compare(payload: CompareIn) -> CompareOut:
    """Сравнить варианты → отсортировано по итоговой цене (USD).

    Каждый item валидируется `CompareItem` (как `DeliveryIn`): битый
    item даёт 422 на входе, а не молча выпадает из ответа.

    Args:
        payload: До 10 вариантов.

    Returns:
        `CompareOut` с результатами, отсортированными по `total_usd`.
    """
    results: list[CompareItemOut] = []
    for it in payload.items:
        _warn_future_year(it.year)
        try:
            b = calc_delivery(
                it.price_usd,
                it.engine_cc,
                it.year,
                region=it.region,
                power_hp=it.power_hp,
            )
        except (ValueError, KeyError) as exc:
            # `CompareItem` уже валидирован Pydantic'ом; сюда попадаем,
            # только если сам `calc_delivery` не осилил (например,
            # `year` вне lookup-таблицы). Клиент получит 400 с контекстом.
            logger.warning(
                "calc_compare_item_failed title=%r year=%s cc=%s region=%s: %s",
                it.title, it.year, it.engine_cc, it.region, exc,
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Не удалось рассчитать вариант {it.title!r}: {exc}",
            ) from exc
        title = it.title or f"{b.year} г. · {b.engine_cc} см³"
        results.append(
            CompareItemOut(
                title=title,
                price_usd=float(b.price_usd),
                year=int(b.year),
                engine_cc=int(b.engine_cc),
                power_hp=b.power_hp,
                region=str(b.region),
                currency=str(b.currency),
                age_group=str(b.age_group),
                total_usd=round(float(b.total_usd), 2),
                total_rub=round(float(b.total_rub), 2),
                total_local=round(float(b.total_local), 2),
            )
        )
    results.sort(key=lambda r: r.total_usd)
    return CompareOut(items=results)