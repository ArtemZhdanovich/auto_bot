"""Поиск похожих машин через LLM-реранк (прод-ревизия 2026-10).

Кандидаты — SQL по БД (та же марка/модель, ±3 года, цена ±35%), финальный
топ с объяснениями выбирает LLM; при недоступности модели — фолбэк по
близости цены. LLM опционален: фабрика может быть не забинджена
(`LLMFactory | None`), тогда всегда работает статистика.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.application.catalog.dto import ListingCard
from app.application.catalog.ports import LLMFactory, ListingReadRepository
from app.application.errors import NotFound
from app.domain.listings.ports import ListingRepository

logger = logging.getLogger(__name__)

JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Сколько кандидатов репозиторий выбирает для реранка.
CANDIDATES_LIMIT = 24

# Причина по умолчанию, если модель не объяснила выбор.
FALLBACK_REASON = "Похожие марка/модель, год и цена"


@dataclass(frozen=True, slots=True)
class SimilarItem:
    """Похожая машина с объяснением, почему она похожа."""

    card: ListingCard
    reason: str
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class SimilarResult:
    """Итог поиска похожих."""

    items: list[SimilarItem] = field(default_factory=list)
    source: str = "stats"  # stats — фолбэк без LLM, ai — с реранком


def _system_prompt(limit: int) -> str:
    """System-промпт реранка: до `limit` машин строгим JSON."""
    return (
        f"Ты — автоэксперт. Отбери до {limit} наиболее похожих на целевое "
        "объявление машин. Ответь СТРОГО JSON: "
        '{"items":[{"id":число,"reason":"краткая причина по-русски","score":0..1}]}'
    )


def _card_fields(card: ListingCard) -> dict[str, Any]:
    """Поля карточки для промпта (минимум, без сырых спеков)."""
    return {
        "id": card.id,
        "make": card.make,
        "model": card.model,
        "year": card.year,
        "price": float(card.price) if card.price is not None else None,
        "mileage_km": card.mileage_km,
    }


def build_messages(
    target: ListingCard, candidates: list[ListingCard], limit: int
) -> list[dict[str, str]]:
    """Собрать пару system/user для реранка.

    Args:
        target: Целевое объявление («найти похожие на него»).
        candidates: Кандидаты от репозитория.
        limit: Сколько машин модель должна отобрать.

    Returns:
        Сообщения в формате `LLMPort`.
    """
    user = (
        "Целевое объявление: "
        + json.dumps(_card_fields(target), ensure_ascii=False)
        + "\nКандидаты: "
        + json.dumps([_card_fields(c) for c in candidates], ensure_ascii=False)
    )
    return [
        {"role": "system", "content": _system_prompt(limit)},
        {"role": "user", "content": user},
    ]


def parse_similar_json(text: str | None) -> list[dict[str, Any]] | None:
    """Достать список `items` из ответа LLM.

    Терпит ```json-заборы и мусор вокруг (как `parse_verdict_json`).

    Args:
        text: Сырой ответ модели.

    Returns:
        Список записей или `None`, если JSON/`items` невалидны.
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
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return None
    return items


def _score(value: Any) -> float:
    """Число 0..1 из ответа модели (мусор/None → 0)."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


class FindSimilar:
    """Похожие объявления для карточки (кнопка «Найти похожие»)."""

    def __init__(
        self,
        listings: ListingRepository,
        read: ListingReadRepository,
        llm_factory: LLMFactory | None = None,
    ) -> None:
        self._listings = listings
        self._read = read
        self._llm_factory = llm_factory

    async def execute(self, listing_id: int, limit: int = 5) -> SimilarResult:
        """Вернуть похожие машины.

        LLM-реранк нужен, только когда кандидатов больше `limit` — иначе
        фолбэк по цене и так возвращает всех кандидатов.

        Raises:
            NotFound: Объявления нет.
        """
        cards = await self._listings.cards_by_ids([listing_id])
        card = cards.get(listing_id)
        if card is None:
            raise NotFound("Listing not found")
        candidates = await self._read.similar_candidates(
            listing_id, limit=CANDIDATES_LIMIT
        )
        if not candidates:
            return SimilarResult()
        if self._llm_factory is not None and len(candidates) > limit:
            reranked = await self._rerank(card, candidates, limit)
            if reranked is not None:
                return SimilarResult(items=reranked, source="ai")
        return self._by_price(card, candidates, limit)

    async def _rerank(
        self, target: ListingCard, candidates: list[ListingCard], limit: int
    ) -> list[SimilarItem] | None:
        """LLM-реранк кандидатов; `None` — модель недоступна/ответ невалиден.

        Карточки берутся из кандидатов (модель выбирает только id), поэтому
        мусорные/чужие id просто пропускаются — фильтрация не нужна.
        """
        by_id = {candidate.id: candidate for candidate in candidates}
        try:
            llm = self._llm_factory()
            reply = await llm.complete(build_messages(target, candidates, limit))
        except Exception:  # noqa: BLE001 — реранк опционален, есть фолбэк
            logger.warning(
                "similar_rerank_llm_failed listing_id=%s", target.id, exc_info=True
            )
            return None

        entries = parse_similar_json(getattr(reply, "text", None))
        if entries is None:
            logger.warning(
                "similar_rerank_bad_json listing_id=%s candidates=%s",
                target.id,
                len(candidates),
            )
            return None

        items: list[SimilarItem] = []
        for entry in entries[:limit]:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id")
            if not isinstance(entry_id, int) or isinstance(entry_id, bool):
                continue
            candidate = by_id.get(entry_id)
            if candidate is None:
                continue
            reason = entry.get("reason")
            items.append(
                SimilarItem(
                    card=candidate,
                    reason=str(reason) if reason else FALLBACK_REASON,
                    score=_score(entry.get("score")),
                )
            )
        return items or None

    def _by_price(
        self, target: ListingCard, candidates: list[ListingCard], limit: int
    ) -> SimilarResult:
        """Фолбэк без LLM: ближайшие по цене."""
        price = float(target.price or 0)
        ordered = sorted(
            candidates,
            key=lambda c: abs(float(c.price or 0) - price) if price else 0,
        )
        items = [
            SimilarItem(
                card=c,
                reason=FALLBACK_REASON,
                score=round(1.0 - i / (len(ordered) + 1), 2),
            )
            for i, c in enumerate(ordered[:limit])
        ]
        return SimilarResult(items=items, source="stats")
