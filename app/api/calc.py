"""Калькулятор доставки Корея → РФ/РБ (MVP: сравнение цен «под ключ»)."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.delivery import DeliveryBreakdown, calc_delivery

router = APIRouter(prefix="/api/calc", tags=["calc"])


class DeliveryIn(BaseModel):
    price_usd: float = Field(gt=0, le=1_000_000)
    engine_cc: int = Field(default=2000, ge=50, le=10_000)
    year: int = Field(ge=1980, le=2100)
    power_hp: int | None = Field(default=None, ge=10, le=2000)
    # Юрисдикция расчёта (валюта объявления: BYN → РБ, RUB → РФ)
    region: Literal["ru", "by"] = "ru"


class DeliveryOut(BaseModel):
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
    components: list[dict]
    legal_refs: list[dict] = []
    total_rub: float
    total_usd: float
    total_local: float
    rates_note: str = "Курс фиксированный (MVP). Логистика/брокер/СБКТС — средние по рынку."


@router.post("/delivery", response_model=DeliveryOut)
async def calc_delivery_endpoint(payload: DeliveryIn) -> DeliveryOut:
    """Расчёт «авто + море + брокер + СБКТС/ЭПТС + пошлина + сборы + утиль» (region: ru|by)."""
    b: DeliveryBreakdown = calc_delivery(
        payload.price_usd, payload.engine_cc, payload.year, region=payload.region, power_hp=payload.power_hp
    )
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


class CompareIn(BaseModel):
    """Сравнение нескольких вариантов «под ключ»."""

    items: list[dict] = Field(min_length=1, max_length=10)


@router.post("/compare")
async def calc_compare(payload: CompareIn) -> dict:
    """Сравнить варианты: [{price_usd, engine_cc, year}, ...] → отсортировано по итогу."""
    results = []
    for it in payload.items:
        try:
            b = calc_delivery(
                float(it["price_usd"]),
                int(it.get("engine_cc", 2000)),
                int(it["year"]),
                region=str(it.get("region", "ru")),
                power_hp=int(it["power_hp"]) if it.get("power_hp") else None,
            )
        except (KeyError, TypeError, ValueError):
            continue
        results.append(
            {
                "title": it.get("title") or f"{b.year} г. · {b.engine_cc} см³",
                "price_usd": b.price_usd,
                "total_usd": round(b.total_usd),
                "total_rub": round(b.total_rub),
                "total_local": round(b.total_local),
                "currency": b.currency,
                "age_group": b.age_group,
            }
        )
    results.sort(key=lambda r: r["total_usd"])
    return {"items": results}
