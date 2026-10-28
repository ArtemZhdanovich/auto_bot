"""Генерация описания лота (12 §2): LLM по полям черновика + лимит.

Продающее описание 400-800 знаков на русском из полей формы SellCar.
Промпт-гварды: только факты из входа, никаких обещаний и гарантий.
Бюджет LLM защищён rate-limit'ом free-тарифа — 5 генераций в день
(Redis INCR + EXPIRE, тот же паттерн, что у чатов 08 §4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.application.catalog.ports import LLMFactory, UserLookupPort
from app.application.errors import AppError, Forbidden, InvalidInput, NotFound
from app.application.market.use_cases import MARKET_GUEST_FORBIDDEN, USER_NOT_FOUND
from app.application.shared.ports import CachePort

logger = logging.getLogger(__name__)

# 5 генераций в день на free-тарифе (12 §2); окно фиксированное.
DESCRIPTION_DAILY_LIMIT = 5
DESCRIPTION_WINDOW_SEC = 24 * 60 * 60
# Ключ Redis: chat:rl:desc:{uid} (общий префикс chat-лимитов).
DESCRIPTION_RATE_PREFIX = "chat:rl:desc"

DESCRIPTION_LIMIT_MESSAGE = (
    "ai_generation_limit: дневной лимит генераций исчерпан "
    "(5 в день на бесплатном тарифе)"
)

DESCRIPTION_SYSTEM_PROMPT = (
    "Ты — копирайтер автомобильных объявлений. Напиши продающее описание "
    "машины на русском языке длиной 400-800 знаков. Используй ТОЛЬКО "
    "факты из входных данных: ничего не выдумывай — ни характеристик, ни "
    "состояния, ни истории. Без обещаний и гарантий («идеальное», "
    "«без вложений», «100%»), без markdown, без заголовков и эмодзи. "
    "Один абзац обычного текста."
)


class DescriptionLimitExceeded(AppError):
    """Дневной лимит генераций free-тарифа исчерпан (429)."""

    code = "rate_limited"
    default_message = DESCRIPTION_LIMIT_MESSAGE


@dataclass(frozen=True, slots=True)
class DescriptionCommand:
    """Вход генерации: поля черновика SellCar (12 §2)."""

    make: str
    model: str
    year: int
    price: Decimal | float | None = None
    currency: str | None = None
    mileage_km: int | None = None
    fuel: str | None = None
    transmission: str | None = None
    photos_count: int = 0


class GenerateDescription:
    """POST /api/market/description: гварды → лимит → LLM → текст."""

    def __init__(
        self,
        users: UserLookupPort,
        llm_factory: LLMFactory,
        cache: CachePort,
    ) -> None:
        self._users = users
        self._llm_factory = llm_factory
        self._cache = cache

    async def execute(
        self, user_id: int, command: DescriptionCommand
    ) -> str:
        """Сгенерировать описание по полям черновика.

        Raises:
            NotFound: Пользователя нет.
            Forbidden: Гостевой аккаунт (генерация после входа).
            DescriptionLimitExceeded: 5 генераций free-тарифа исчерпаны.
            AiUnavailable: LLM не сконфигурирована.
        """
        profile = await self._users.get(user_id)
        if profile is None:
            raise NotFound(USER_NOT_FOUND)
        if profile.is_guest:
            raise Forbidden(MARKET_GUEST_FORBIDDEN)
        if profile.tier == "free":
            await self._enforce_limit(user_id)

        llm = self._llm_factory()
        reply = await llm.complete(
            [
                {"role": "system", "content": DESCRIPTION_SYSTEM_PROMPT},
                {"role": "user", "content": self._user_prompt(command)},
            ]
        )
        text = str(getattr(reply, "text", "") or "").strip()
        if len(text) < 40:
            raise InvalidInput(
                "description_generation_failed: модель вернула пустой ответ"
            )
        logger.info(
            "market_description_generated user_id=%s tier=%s len=%d",
            user_id,
            profile.tier,
            len(text),
        )
        return text[:2000]

    async def _enforce_limit(self, user_id: int) -> None:
        """Поднять счётчик окна и отлупить сверх лимита (fail-open).

        Лежачий Redis не блокирует генерацию (как у чат-лимитов 08 §4).
        """
        key = f"{DESCRIPTION_RATE_PREFIX}:{user_id}"
        try:
            count = await self._cache.incr(key)
            if count == 1:
                await self._cache.expire(key, DESCRIPTION_WINDOW_SEC)
        except Exception:  # noqa: BLE001 — Redis не должен ломать фичу
            logger.warning("description_rate_limit_failed", exc_info=True)
            return
        if count > DESCRIPTION_DAILY_LIMIT:
            logger.warning(
                "description_limit_exceeded user_id=%s count=%d", user_id, count
            )
            raise DescriptionLimitExceeded()

    @staticmethod
    def _user_prompt(command: DescriptionCommand) -> str:
        """Входные поля → компактный промпт (только факты, без фантазий)."""
        price = (
            f"{float(command.price):.0f} {command.currency}"
            if command.price is not None
            else "не указана"
        )
        parts = [
            f"Марка: {command.make}",
            f"Модель: {command.model}",
            f"Год: {command.year}",
            f"Цена: {price}",
        ]
        if command.mileage_km is not None:
            parts.append(f"Пробег: {command.mileage_km} км")
        if command.fuel:
            parts.append(f"Топливо: {command.fuel}")
        if command.transmission:
            parts.append(f"Коробка: {command.transmission}")
        parts.append(f"Фотографий в объявлении: {command.photos_count}")
        return "\n".join(parts)
