"""AI-анализ стоимости: LLM-разбор + статистика по рынку (comps + история).

Монетизация: free — 1 разбор одной машины в неделю; pro/business — безлимит.
LLM недоступен/мусорный ответ → статистический вердикт (фича не падает).
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm_client import BaseLLMClient
from app.db.models import Listing, ListingPriceHistory, PriceAnalysis

logger = logging.getLogger(__name__)

QUOTA_WINDOW_DAYS = 7


class QuotaExceeded(Exception):
    """Бесплатный разбор этой машины на этой неделе уже израсходован."""


# ---------------------------------------------------------------- статистика


def _stats(prices: list[float]) -> dict[str, float | int] | None:
    if len(prices) < 2:
        return None
    return {
        "count": len(prices),
        "min": round(min(prices)),
        "max": round(max(prices)),
        "median": round(median(prices)),
    }


def _verdict_from_stats(price: float, comps: dict[str, Any] | None) -> str:
    """Статический вердикт без LLM: цена против медианы рынка."""
    if not comps:
        return "fair"
    med = float(comps.get("median") or 0)
    if not med:
        return "fair"
    ratio = price / med
    if ratio <= 0.93:
        return "deal"
    if ratio >= 1.07:
        return "overpriced"
    return "fair"


async def collect_context(
    session: AsyncSession, listing: Listing
) -> dict[str, Any]:
    """Данные для разбора: comps (та же модель, ±2 года) и история цен."""
    prices: list[float] = []
    if listing.make and listing.model and listing.year:
        result = await session.execute(
            select(Listing.price)
            .where(
                Listing.id != listing.id,
                Listing.price.isnot(None),
                Listing.price > 0,
                func.lower(Listing.make) == listing.make.lower(),
                func.lower(Listing.model) == listing.model.lower(),
                Listing.year.between(listing.year - 2, listing.year + 2),
            )
            .limit(30)
        )
        prices = [float(p) for (p,) in result.all()]

    hist_result = await session.execute(
        select(ListingPriceHistory.price, ListingPriceHistory.recorded_at)
        .where(ListingPriceHistory.listing_id == listing.id)
        .order_by(ListingPriceHistory.recorded_at, ListingPriceHistory.id)
    )
    history = [
        {"price": float(p), "recorded_at": str(ts)}
        for p, ts in hist_result.all()
    ]

    return {
        "comps": _stats(prices),
        "comp_prices": prices,
        "history": history,
    }


# ---------------------------------------------------------------------- LLM


def _build_prompt(listing: Listing, ctx: dict[str, Any]) -> list[dict[str, str]]:
    price = float(listing.price or 0)
    comps = ctx.get("comps")
    system = (
        "Ты — опытный автоэксперт по рынку Кореи (encar/kcar). Оцени объявление: "
        "справедлива ли цена относительно рынка. Ответь СТРОГО одним JSON-объектом "
        'без markdown: {"verdict": "deal|fair|overpriced", "fair_min": число, '
        '"fair_max": число, "summary": "1-2 предложения по-русски", '
        '"factors": ["до 4 коротких факторов цены"]}. fair_min/fair_max — в '
        "валюте объявления."
    )
    user = (
        f"Объявление: {listing.make} {listing.model}, {listing.year} г., "
        f"{listing.mileage_km or '?'} км, цена {price:.0f} {listing.currency or 'KRW'}.\n"
        f"Рынок (сопоставимые): {json.dumps(comps, ensure_ascii=False)}.\n"
        f"История цены объявления: {json.dumps(ctx.get('history', []), ensure_ascii=False)}."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_llm_json(text: str | None) -> dict[str, Any] | None:
    """Достать JSON-объект из ответа LLM (терпит ```json-заборы и мусор)."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in (
        "deal",
        "fair",
        "overpriced",
    ):
        return None
    return data


# ------------------------------------------------------------------- квоты


async def quota_left(session: AsyncSession, user_id: int, listing_id: int) -> int:
    """Сколько бесплатных разборов этой машины осталось у юзера (окно 7 дней)."""
    since = datetime.now(UTC) - timedelta(days=QUOTA_WINDOW_DAYS)
    result = await session.execute(
        select(func.count())
        .select_from(PriceAnalysis)
        .where(
            PriceAnalysis.user_id == user_id,
            PriceAnalysis.listing_id == listing_id,
            PriceAnalysis.created_at >= since,
        )
    )
    used = int(result.scalar_one())
    return max(0, 1 - used)


# ------------------------------------------------------------------- фасад


async def run_analysis(
    session: AsyncSession,
    llm: BaseLLMClient,
    listing: Listing,
    user_id: int,
    *,
    tier: str = "free",
) -> dict[str, Any]:
    """Полный разбор: квота → LLM/статистика → сохранить → payload для UI."""
    if tier == "free" and await quota_left(session, user_id, listing.id) <= 0:
        raise QuotaExceeded

    ctx = await collect_context(session, listing)
    price = float(listing.price or 0)

    llm_data: dict[str, Any] | None = None
    try:
        result = await llm.complete(_build_prompt(listing, ctx))
        llm_data = parse_llm_json(result.text)
    except Exception:  # noqa: BLE001 — LLM не обязателен, есть статистика
        logger.warning("price_analysis_llm_failed listing=%s", listing.id, exc_info=True)

    verdict = (llm_data or {}).get("verdict") or _verdict_from_stats(price, ctx.get("comps"))
    fair_min = _num((llm_data or {}).get("fair_min"))
    fair_max = _num((llm_data or {}).get("fair_max"))
    if fair_min is None and fair_max is None and ctx.get("comps"):
        comps = ctx["comps"]
        med = float(comps["median"])
        fair_min, fair_max = round(med * 0.93), round(med * 1.07)

    factors = (llm_data or {}).get("factors") or []
    if not factors and ctx.get("comps"):
        comps = ctx["comps"]
        factors = [
            f"{comps['count']} сопоставимых объявлений: "
            f"{comps['min']}–{comps['max']}, медиана {comps['median']}"
        ]

    payload: dict[str, Any] = {
        "verdict": verdict,
        "price": price,
        "currency": listing.currency or "KRW",
        "fair_min": fair_min,
        "fair_max": fair_max,
        "summary": (llm_data or {}).get("summary") or "Оценка по статистике рынка.",
        "factors": factors[:4],
        "comps": ctx.get("comps"),
        "history": ctx.get("history", []),
        "source": "ai" if llm_data else "stats",
        "created_at": datetime.now(UTC).isoformat(),
    }

    row = PriceAnalysis(
        listing_id=listing.id,
        user_id=user_id,
        verdict=verdict,
        fair_min=fair_min,
        fair_max=fair_max,
        summary=payload["summary"],
        payload=payload,
    )
    session.add(row)
    await session.commit()
    return payload


def _num(v: Any) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(f) if f > 0 else None


async def latest_analysis(session: AsyncSession, listing_id: int) -> dict[str, Any] | None:
    """Кэш последнего разбора листинга (общий для всех юзеров)."""
    result = await session.execute(
        select(PriceAnalysis)
        .where(PriceAnalysis.listing_id == listing_id)
        .order_by(PriceAnalysis.created_at.desc(), PriceAnalysis.id.desc())
        .limit(1)
    )
    row = result.scalars().first()
    return dict(row.payload or {}) if row else None
