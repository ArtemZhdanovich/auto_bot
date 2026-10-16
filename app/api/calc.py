"""Калькулятор доставки Корея → РФ (MVP: сравнение цен «под ключ»)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.delivery import DeliveryBreakdown, calc_delivery

router = APIRouter(prefix="/api/calc", tags=["calc"])


class DeliveryIn(BaseModel):
    price_usd: float = Field(gt=0, le=1_000_000)
    engine_cc: int = Field(default=2000, ge=50, le=10_000)
    year: int = Field(ge=1980, le=2100)


class DeliveryOut(BaseModel):
    price_usd: float
    engine_cc: int
    year: int
    age_years: int
    age_group: str
    components: list[dict]
    legal_refs: list[dict] = []
    total_rub: float
    total_usd: float
    rates_note: str = "Курс фиксированный (MVP). Логистика/брокер/СБКТС — средние по рынку."


@router.post("/delivery", response_model=DeliveryOut)
async def calc_delivery_endpoint(payload: DeliveryIn) -> DeliveryOut:
    """Расчёт «авто + море + брокер + СБКТС/ЭПТС + пошлина + сборы + утиль»."""
    b: DeliveryBreakdown = calc_delivery(
        payload.price_usd, payload.engine_cc, payload.year
    )
    return DeliveryOut(
        price_usd=b.price_usd,
        engine_cc=b.engine_cc,
        year=b.year,
        age_years=b.age_years,
        age_group=b.age_group,
        components=b.components,
        legal_refs=b.legal_refs,
        total_rub=b.total_rub,
        total_usd=b.total_usd,
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
                float(it["price_usd"]), int(it.get("engine_cc", 2000)), int(it["year"])
            )
        except (KeyError, TypeError, ValueError):
            continue
        results.append(
            {
                "title": it.get("title") or f"{b.year} г. · {b.engine_cc} см³",
                "price_usd": b.price_usd,
                "total_usd": round(b.total_usd),
                "total_rub": round(b.total_rub),
                "age_group": b.age_group,
            }
        )
    results.sort(key=lambda r: r["total_usd"])
    return {"items": results}
