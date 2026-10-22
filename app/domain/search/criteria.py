"""SearchCriteria и SourceConfig — единые контракты поиска и источников (ТЗ п. 5.2, 5.5, 5.7)."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class SearchCriteria(BaseModel):
    # Основное
    make: str | None = None
    model: str | None = None
    generation: str | None = None

    # Годы
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)

    # Цена
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None

    # Пробег
    mileage_max: int | None = Field(default=None, ge=0)

    # Характеристики
    fuel: str | None = None
    transmission: str | None = None
    drive: str | None = None
    body_type: str | None = None
    color: str | None = None
    engine_volume_min: float | None = Field(default=None, ge=0, le=20)
    engine_volume_max: float | None = Field(default=None, ge=0, le=20)

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

    @model_validator(mode="after")
    def _check_ranges(self) -> "SearchCriteria":
        """Кросс-полевые инварианты: min не больше max."""
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from must be <= year_to")
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min must be <= price_max")
        if (
            self.engine_volume_min is not None
            and self.engine_volume_max is not None
            and self.engine_volume_min > self.engine_volume_max
        ):
            raise ValueError("engine_volume_min must be <= engine_volume_max")
        return self


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

    @field_validator("retry_policy", "capabilities", "config", mode="before")
    @classmethod
    def _none_to_default(cls, v: Any, info: ValidationInfo) -> Any:
        """NULL-колонки БД → дефолты (иначе GET /sources падает с 500)."""
        if v is None:
            return {"retry_policy": {"max": 3, "backoff": "exponential"}}.get(
                info.field_name, {}
            )
        return v


class SourceHealth(BaseModel):
    source_name: str
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    error_streak: int = 0
    last_error: str | None = None
    avg_latency_ms: int | None = None
    total_fetched: int = 0
