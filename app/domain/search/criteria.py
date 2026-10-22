"""SearchCriteria и SourceConfig — единые контракты поиска и источников (ТЗ п. 5.2, 5.5, 5.7)."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

# TECHDEBT 1.3 (prompt injection): строковые поля попадают в system-prompt
# LLM (`build_messages`), поэтому — без переводов строк (ломают структуру
# промпта) и с разумным потолком длины (не дают раздуть контекст/токены).
# Числам инъекция не страшна — они ограничены ge/le.
# Якоря ^$ обязательны: pydantic проверяет pattern в search-режиме,
# без них `[^\n\r]*` сматчил бы пустой префикс любой строки.
_NoNewlines = StringConstraints(pattern=r"^[^\n\r]*$", max_length=100)
SafeStr = Annotated[str, _NoNewlines]
SafeStrShort = Annotated[str, StringConstraints(pattern=r"^[^\n\r]*$", max_length=50)]
# Свободный текст пользователя: переводы строк разрешены, длина ограничена.
NotesStr = Annotated[str, StringConstraints(max_length=1000)]


class SearchCriteria(BaseModel):
    # Основное
    make: SafeStr | None = None
    model: SafeStr | None = None
    generation: SafeStr | None = None

    # Годы
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)

    # Цена
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    currency: Annotated[str, StringConstraints(pattern=r"^[^\n\r]*$", max_length=8)] | None = None

    # Пробег
    mileage_max: int | None = Field(default=None, ge=0)

    # Характеристики
    fuel: SafeStrShort | None = None
    transmission: SafeStrShort | None = None
    drive: SafeStrShort | None = None
    body_type: SafeStrShort | None = None
    color: SafeStrShort | None = None
    engine_volume_min: float | None = Field(default=None, ge=0, le=20)
    engine_volume_max: float | None = Field(default=None, ge=0, le=20)

    # География и источник
    country: SafeStrShort | None = None  # "KR", "DE", "US", "CN", "JP"
    sources: list[SafeStrShort] | None = Field(default=None, max_length=20)
    location: SafeStr | None = None

    # Состояние
    not_damaged: bool | None = None
    no_accidents: bool | None = None
    seller_type: SafeStrShort | None = None

    # Свободные заметки
    notes: NotesStr | None = None

    # Метаданные
    created_from_dialog: bool = False
    dialog_session_id: SafeStr | None = None

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
