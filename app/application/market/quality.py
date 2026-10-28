"""«Качество объявления» (12 §2): чистая функция без LLM.

Правила штрафов от базы 100: нет фото −30, фото <3 −25, короткое
описание (<80 знаков) −20, цена вне ±20% медианы модели −15. Возвращает
`{score, tips}` для my-lots и превью SellCar (шаг «Цена и публикация»).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.domain.pricing.ports import ListingQuery
from app.domain.pricing.rules import market_stats

if TYPE_CHECKING:
    from app.application.market.dto import CreateLotCommand, MarketLotSnapshot
    from app.domain.pricing.ports import PriceAnalysisRepository

# Штрафы/пороги правил (12 §2).
PHOTOS_NONE_PENALTY = 30
PHOTOS_FEW_PENALTY = 25
PHOTOS_FEW_THRESHOLD = 3
DESCRIPTION_SHORT_PENALTY = 20
DESCRIPTION_MIN_LEN = 80
PRICE_OFF_MARKET_PENALTY = 15
# Допустимое отклонение цены от медианы модели (доля).
PRICE_OFF_MARKET_RATIO = 0.2

TIP_PHOTOS_NONE = "Добавьте фотографии — объявления без фото почти не смотрят"
TIP_PHOTOS_FEW = "Добавьте фото кузова и салона (минимум 3)"
TIP_DESCRIPTION_SHORT = "Дополните описание — минимум 80 знаков"
TIP_PRICE_ABOVE = "Цена выше рынка"
TIP_PRICE_BELOW = "Цена ниже рынка"


def listing_quality(
    *,
    photos_count: int,
    description_len: int,
    price: Decimal | float | None = None,
    currency: str | None = None,
    comps_median: Decimal | float | None = None,
    make: str | None = None,
    model: str | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    """Оценка качества объявления по правилам 12 §2.

    Args:
        photos_count: Сколько фото у лота.
        description_len: Длина описания владельца (знаков).
        price: Цена лота (для сверки с рынком).
        currency: Валюта цены (для текста подсказки).
        comps_median: Медиана сопоставимых объявлений (может не быть).
        make: Марка (для контекста, не влияет на score).
        model: Модель (для контекста).
        year: Год (для контекста).

    Returns:
        `{score: 0-100, tips: [строки-советы]}`.
    """
    score = 100
    tips: list[str] = []

    if photos_count <= 0:
        score -= PHOTOS_NONE_PENALTY
        tips.append(TIP_PHOTOS_NONE)
    elif photos_count < PHOTOS_FEW_THRESHOLD:
        score -= PHOTOS_FEW_PENALTY
        tips.append(TIP_PHOTOS_FEW)

    if description_len < DESCRIPTION_MIN_LEN:
        score -= DESCRIPTION_SHORT_PENALTY
        tips.append(TIP_DESCRIPTION_SHORT)

    median = float(comps_median) if comps_median is not None else 0.0
    lot_price = float(price) if price is not None else 0.0
    if median > 0 and lot_price > 0:
        # Медиана в текст подсказки — «Цена выше рынка (~31 000 USD)».
        market = f"(~{round(median):,} {currency or ''})".replace(",", " ")
        ratio = lot_price / median
        if ratio > 1 + PRICE_OFF_MARKET_RATIO:
            score -= PRICE_OFF_MARKET_PENALTY
            tips.append(f"{TIP_PRICE_ABOVE} {market}")
        elif ratio < 1 - PRICE_OFF_MARKET_RATIO:
            score -= PRICE_OFF_MARKET_PENALTY
            tips.append(f"{TIP_PRICE_BELOW} {market}")

    return {"score": max(0, min(100, score)), "tips": tips}


def command_quality(command: "CreateLotCommand") -> dict[str, Any]:
    """Качество лота в момент создания (поля формы SellCar, 12 §2)."""
    return listing_quality(
        photos_count=len(command.photos or ()),
        description_len=len(command.description or ""),
        price=command.price,
        currency=command.currency,
        make=command.make,
        model=command.model,
        year=command.year,
    )


async def comps_median(
    analyses: "PriceAnalysisRepository | None", lot: "MarketLotSnapshot"
) -> float | None:
    """Медиана сопоставимых цен лота (тот же контекст, что у AI-разбора).

    `None` — репозитория нет, у лота нет make/model/year или сопоставимых
    меньше двух; ошибки чтения не роняют my-lots (best-effort).
    """
    if analyses is None or not (lot.make and lot.model and lot.year):
        return None
    try:
        ctx = await analyses.context(
            ListingQuery(
                id=lot.listing_id, make=lot.make, model=lot.model, year=lot.year
            )
        )
    except Exception:  # noqa: BLE001 — рынок опционален для качества
        return None
    stats = market_stats(list(ctx.comp_prices))
    return float(stats["median"]) if stats else None


async def lot_quality(
    lot: "MarketLotSnapshot", analyses: "PriceAnalysisRepository | None" = None
) -> dict[str, Any]:
    """Качество снятого лота (my-lots/PATCH) с медианой рынка."""
    return listing_quality(
        photos_count=len(lot.images or ()),
        description_len=len(lot.owner_description or ""),
        price=lot.price,
        currency=lot.currency,
        comps_median=await comps_median(analyses, lot),
        make=lot.make,
        model=lot.model,
        year=lot.year,
    )
