"""SearchCriteria и SourceConfig — единые контракты поиска и источников (ТЗ п. 5.2, 5.5, 5.7)."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel


class SearchCriteria(BaseModel):
    # Основное
    make: str | None = None
    model: str | None = None
    generation: str | None = None

    # Годы
    year_from: int | None = None
    year_to: int | None = None

    # Цена
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    currency: str | None = None

    # Пробег
    mileage_max: int | None = None

    # Характеристики
    fuel: str | None = None
    transmission: str | None = None
    drive: str | None = None
    body_type: str | None = None
    color: str | None = None
    engine_volume_min: float | None = None
    engine_volume_max: float | None = None

    # География и источник
    country: str | None = None  # "KR", "DE", "US", "CN", "JP"
    sources: list[str] | None = None  # явный список источников
    location: str | None = None

    # Состояние
    not_damaged: bool | None = None
    no_accidents: bool | None = None
    seller_type: str | None = None

    # Свободные заметки
    notes: str | None = None

    # Метаданные
    created_from_dialog: bool = False
    dialog_session_id: str | None = None


class SourceConfig(BaseModel):
    name: str
    adapter_class: str
    kind: Literal["api", "parser", "hybrid"]
    enabled: bool = True
    priority: int = 100
    schedule_cron: str | None = None
    rate_limit_per_min: int = 60
    max_concurrency: int = 3
    timeout_sec: int = 30
    retry_policy: dict[str, Any] = {"max": 3, "backoff": "exponential"}
    credentials_ref: str | None = None
    capabilities: dict[str, Any] = {}
    config: dict[str, Any] = {}


class SourceHealth(BaseModel):
    source_name: str
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    error_streak: int = 0
    last_error: str | None = None
    avg_latency_ms: int | None = None
    total_fetched: int = 0
