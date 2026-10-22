"""DTO разбора цены: вход сценария и результат для transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ListingSnapshot:
    """Поля объявления, нужные разбору (без ORM-объекта)."""

    id: int
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: float | None = None
    currency: str | None = None
    mileage_km: int | None = None


@dataclass(frozen=True, slots=True)
class PriceAnalysisResult:
    """Готовый разбор цены; `to_payload` — контракт ответа API."""

    verdict: str
    price: float
    currency: str
    summary: str
    fair_min: float | None = None
    fair_max: float | None = None
    factors: tuple[str, ...] = ()
    comps: dict[str, Any] | None = None
    history: tuple[dict[str, Any], ...] = ()
    source: str = "stats"
    created_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Собрать JSON-совместимый ответ (поля — контракт `AnalysisOut`)."""
        return {
            "verdict": self.verdict,
            "price": self.price,
            "currency": self.currency,
            "fair_min": self.fair_min,
            "fair_max": self.fair_max,
            "summary": self.summary,
            "factors": list(self.factors),
            "comps": self.comps,
            "history": list(self.history),
            "source": self.source,
            "created_at": self.created_at,
        }
