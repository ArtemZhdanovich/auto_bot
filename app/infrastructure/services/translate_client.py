"""Фасад перевода тайтлов (обратная совместимость).

Код по слоям:
- сценарий (батч, circuit breaker, fallback) — `application/translation/`;
- httpx и Redis-кэш — `infrastructure/translation/client.py`;
- глоссарий (домен) — `domain/translation/glossary.py`, применяет вызывающий.

Обязательства (по итогам выбора размещения NLLB):
- батчевый POST /translate {texts, source_lang, target_lang};
- кэш переводов в Redis: tr:{target}:{src}:{sha1(title)}, TTL 30 дней —
  корейские/китайские заголовки повторяются массово, кэш экономит 90%+ CPU;
- retry с backoff (сетевые ошибки; HTTP 4xx/5xx не ретраим);
- circuit breaker: N ошибок подряд → пауза (алерт в Telegram);
- перевод НЕкритичен: при любой ошибке возвращаются None → UI показывает
  оригинальный title (fallback обязателен).
"""

import logging

import httpx
from redis.asyncio import Redis

from app.application.translation.use_cases import TranslateTexts
from app.config import get_settings
from app.domain.translation.errors import TranslationUnavailable
from app.infrastructure.cache.redis import RedisCache
from app.infrastructure.services.alerting import fire, send_alert
from app.infrastructure.translation.client import (
    CachedTranslationClient,
    HttpTranslateClient,
    cache_key,
)

__all__ = ["TranslateClient", "TranslateUnavailable", "translate_client_from_settings"]

logger = logging.getLogger(__name__)

# Прежнее публичное имя ошибки (импортировалось из этого модуля).
TranslateUnavailable = TranslationUnavailable


class TranslateClient:
    """Переводчик для fetch-воркера: сборка порта, кэша и сценария."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        enabled: bool = True,
        timeout: float = 30.0,
        cache_ttl: int = 60 * 60 * 24 * 30,
        redis: Redis | None = None,
        max_retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_token = api_token
        self.enabled = bool(enabled and base_url)
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.redis = redis
        self._max_retries = max_retries
        self._transport = transport

        settings = get_settings()
        translator = HttpTranslateClient(
            base_url=self.base_url,
            api_token=api_token,
            timeout=timeout,
            max_retries=max_retries,
            transport=transport,
        )
        if redis is not None:
            translator = CachedTranslationClient(
                translator, RedisCache(redis), ttl_sec=cache_ttl
            )
        self._use_case = TranslateTexts(
            translator,
            enabled=self.enabled,
            fail_threshold=settings.translate_cb_fail_threshold,
            cooldown_sec=settings.translate_cb_cooldown_sec,
            on_open=lambda: fire(
                send_alert(
                    f"🔴 Translate circuit breaker открыт на "
                    f"{settings.translate_cb_cooldown_sec}s "
                    f"({settings.translate_cb_fail_threshold} ошибок подряд) — "
                    f"тайтлы без перевода.",
                    dedup_key="cb:translate",
                )
            ),
        )

    def _cache_key(self, text: str, source_lang: str, target_lang: str) -> str:
        """Ключ кэша перевода (совместимость с прежним API клиента)."""
        return cache_key(text, source_lang, target_lang)

    async def translate(
        self,
        texts: list[str],
        source_lang: str = "auto",
        target_lang: str | None = None,
    ) -> list[str | None]:
        """Перевести батч текстов; при ошибке — None (показываем оригинал)."""
        target = target_lang or get_settings().translate_target_lang
        return await self._use_case.execute(texts, source_lang, target)


def translate_client_from_settings(redis: Redis | None = None) -> TranslateClient:
    """Сборка клиента из настроек (для fetch-воркера)."""
    s = get_settings()
    return TranslateClient(
        base_url=s.translate_service_url,
        api_token=s.translate_api_token,
        enabled=s.translate_enabled,
        timeout=s.translate_timeout_sec,
        cache_ttl=s.translate_cache_ttl_sec,
        redis=redis,
        # Docker Desktop: embedded-DNS/сеть флапает — 2 попыток мало, иначе
        # батч целиком уходит в None и падает в circuit breaker на 5 минут
        max_retries=4,
    )
