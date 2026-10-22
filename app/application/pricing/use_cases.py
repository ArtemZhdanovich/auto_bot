"""Сценарий AI-разбора цены: квота → контекст рынка → LLM → сохранение.

LLM нужен только для «человеческого» объяснения: вердикт, диапазон и
факторы считаются доменными правилами, поэтому падение модели не ломает
фичу — остаётся статистический разбор.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.application.catalog.dto import (
    AnalyzePriceCommand,
    ListingSnapshot,
    PriceAnalysisResult,
)
from app.application.shared.ports import LLMPort
from app.application.shared.ports import UnitOfWork
from app.domain.pricing.ports import (
    ListingQuery,
    MarketContext,
    PriceAnalysisRecord,
    PriceAnalysisRepository,
)
from app.domain.pricing.rules import (
    FACTORS_LIMIT,
    VERDICTS,
    ensure_quota,
    fair_range,
    market_stats,
    normalize_amount,
    stats_factor,
    verdict_from_stats,
)

logger = logging.getLogger(__name__)

STATS_SUMMARY = "Оценка по статистике рынка."
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_PROMPT = (
    "Ты — опытный автоэксперт по рынку Кореи (encar/kcar). Оцени объявление: "
    "справедлива ли цена относительно рынка. Ответь СТРОГО одним JSON-объектом "
    'без markdown: {"verdict": "deal|fair|overpriced", "fair_min": число, '
    '"fair_max": число, "summary": "1-2 предложения по-русски", '
    '"factors": ["до 4 коротких факторов цены"]}. fair_min/fair_max — в '
    "валюте объявления."
)


def build_messages(
    listing: ListingSnapshot,
    comps: dict[str, Any] | None,
    history: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Собрать пару system/user для разбора.

    Args:
        listing: Объявление.
        comps: Сводка рынка (`market_stats`), может быть `None`.
        history: История цены объявления.

    Returns:
        Сообщения в формате `LLMPort`.
    """
    price = float(listing.price or 0)
    user = (
        f"Объявление: {listing.make} {listing.model}, {listing.year} г., "
        f"{listing.mileage_km or '?'} км, цена {price:.0f} "
        f"{listing.currency or 'KRW'}.\n"
        f"Рынок (сопоставимые): {json.dumps(comps, ensure_ascii=False)}.\n"
        f"История цены объявления: {json.dumps(list(history), ensure_ascii=False)}."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_verdict_json(text: str | None) -> dict[str, Any] | None:
    """Достать JSON-объект вердикта из ответа LLM.

    Терпит ```json-заборы и мусор вокруг; пропускает только известные
    вердикты — иначе мусор модели уехал бы в БД (CHECK на verdict).

    Args:
        text: Сырой ответ модели.

    Returns:
        Разобранный объект или `None`, если JSON/вердикт невалиден.
    """
    if not text:
        return None
    match = JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in VERDICTS:
        return None
    return data


def _factors(value: Any, comps: dict[str, Any] | None) -> tuple[str, ...]:
    """Факторы цены: из LLM, иначе — один факт о рынке."""
    items = value if isinstance(value, (list, tuple)) else ()
    result = tuple(str(item) for item in items[:FACTORS_LIMIT])
    return result or tuple(stats_factor(comps))


class AnalyzePrice:
    """Разбор цены объявления с сохранением результата в кэш."""

    def __init__(
        self,
        analyses: PriceAnalysisRepository,
        llm: LLMPort,
        uow: UnitOfWork,
    ) -> None:
        self._analyses = analyses
        self._llm = llm
        self._uow = uow

    async def execute(self, command: AnalyzePriceCommand) -> PriceAnalysisResult:
        """Выполнить разбор и сохранить его.

        Args:
            command: Объявление, пользователь, тариф и остаток квоты.

        Returns:
            Готовый разбор для transport.

        Raises:
            QuotaExceeded: Бесплатный лимит на эту машину исчерпан.
        """
        ensure_quota(command.tier, command.free_quota_left)

        listing = command.listing
        ctx = await self._analyses.context(
            ListingQuery(
                id=listing.id,
                make=listing.make,
                model=listing.model,
                year=listing.year,
            )
        )
        comps = market_stats(list(ctx.comp_prices))
        price = float(listing.price or 0)
        llm_data = await self._ask_llm(listing, comps, ctx)

        result = self._build_result(listing, price, comps, ctx, llm_data)
        async with self._uow:
            await self._analyses.save(
                PriceAnalysisRecord(
                    listing_id=listing.id,
                    user_id=command.user_id,
                    verdict=result.verdict,
                    fair_min=result.fair_min,
                    fair_max=result.fair_max,
                    summary=result.summary,
                    payload=result.to_payload(),
                )
            )
            await self._uow.commit()
        return result

    async def _ask_llm(
        self,
        listing: ListingSnapshot,
        comps: dict[str, Any] | None,
        ctx: MarketContext,
    ) -> dict[str, Any] | None:
        """Спросить модель; любой сбой — `None` (остаётся статистика)."""
        try:
            reply = await self._llm.complete(build_messages(listing, comps, ctx.history))
        except Exception:  # noqa: BLE001 — LLM не обязателен, есть статистика
            logger.warning(
                "price_analysis_llm_failed listing=%s", listing.id, exc_info=True
            )
            return None
        return parse_verdict_json(getattr(reply, "text", None))

    def _build_result(
        self,
        listing: ListingSnapshot,
        price: float,
        comps: dict[str, Any] | None,
        ctx: MarketContext,
        llm_data: dict[str, Any] | None,
    ) -> PriceAnalysisResult:
        """Свести ответ LLM и статистику рынка в один вердикт."""
        data = llm_data or {}
        fair_min = normalize_amount(data.get("fair_min"))
        fair_max = normalize_amount(data.get("fair_max"))
        if fair_min is None and fair_max is None and comps:
            fair_min, fair_max = fair_range(comps)
        return PriceAnalysisResult(
            verdict=data.get("verdict") or verdict_from_stats(price, comps),
            price=price,
            currency=listing.currency or "KRW",
            summary=data.get("summary") or STATS_SUMMARY,
            fair_min=fair_min,
            fair_max=fair_max,
            factors=_factors(data.get("factors"), comps),
            comps=comps,
            history=ctx.history,
            source="ai" if llm_data else "stats",
            created_at=datetime.now(UTC).isoformat(),
        )
