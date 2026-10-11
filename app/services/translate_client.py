"""TranslateClient — обёртка над services/translate (PLAN.md, фаза 5).

Обязательства (по итогам выбора размещения NLLB):
- батчевый POST /translate {texts, source_lang, target_lang};
- кэш переводов в Redis: tr:{target}:{src}:{sha1(title)}, TTL 30 дней —
  корейские/китайские заголовки повторяются массово, кэш экономит 90%+ CPU;
- retry с backoff (сетевые ошибки; HTTP 4xx/5xx не ретраим);
- circuit breaker: N ошибок подряд → пауза (алерт в Telegram);
- перевод НЕкритичен: при любой ошибке возвращаются None → UI показывает
  оригинальный title (fallback обязателен).
"""

import asyncio
import hashlib
import logging
import time

import httpx
from redis.asyncio import Redis

from app.core.config import get_settings
from app.services.alerting import fire, send_alert

logger = logging.getLogger(__name__)


class TranslateUnavailable(RuntimeError):
    """Сервис перевода недоступен / CB открыт / ответ невалиден."""


class TranslateClient:
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
        # Circuit breaker (in-memory, на процесс)
        self._consecutive_failures = 0
        self._open_until = 0.0

    # --- circuit breaker -------------------------------------------------

    def _breaker_open(self) -> bool:
        return time.monotonic() < self._open_until

    def _record_failure(self, fail_threshold: int, cooldown: int) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= fail_threshold:
            self._open_until = time.monotonic() + cooldown
            self._consecutive_failures = 0
            logger.warning("translate_circuit_breaker_open: cooldown=%ss", cooldown)
            fire(
                send_alert(
                    f"🔴 Translate circuit breaker открыт на {cooldown}s "
                    f"({fail_threshold} ошибок подряд) — тайтлы без перевода.",
                    dedup_key="cb:translate",
                )
            )

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = 0.0

    # --- cache / request ---------------------------------------------------

    def _cache_key(self, text: str, source_lang: str, target_lang: str) -> str:
        digest = hashlib.sha1(text.encode()).hexdigest()
        return f"tr:{target_lang}:{source_lang}:{digest}"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_token:
            h["Authorization"] = f"Bearer {self.api_token}"
        return h

    async def _request(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str | None]:
        payload = {
            "texts": texts,
            "source_lang": source_lang,
            "target_lang": target_lang,
        }
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self._transport
        ) as client:
            last_exc: Exception | None = None
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.post(
                        f"{self.base_url}/translate",
                        json=payload,
                        headers=self._headers(),
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_exc = exc
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.5 * 2**attempt)
                        continue
                    raise TranslateUnavailable(
                        f"translate_network_error: {exc}"
                    ) from exc
                if resp.status_code >= 400:
                    # HTTP-ошибка сервиса финальна — не ретраим
                    raise TranslateUnavailable(f"translate_http_{resp.status_code}")
                translations = resp.json().get("translations")
                if not isinstance(translations, list) or len(translations) != len(texts):
                    raise TranslateUnavailable("translate_bad_response")
                return translations
        raise TranslateUnavailable(f"translate_network_error: {last_exc}")

    async def translate(
        self,
        texts: list[str],
        source_lang: str = "auto",
        target_lang: str | None = None,
    ) -> list[str | None]:
        """Перевести батч текстов; при ошибке — None (показываем оригинал)."""
        target = target_lang or get_settings().translate_target_lang
        results: list[str | None] = [None] * len(texts)
        if not self.enabled or not texts:
            return results

        keys = [self._cache_key(t, source_lang, target) for t in texts]
        cached: list[str | None] = [None] * len(texts)
        if self.redis is not None:
            values = await self.redis.mget(*keys)
            cached = [v.decode() if isinstance(v, bytes) else v for v in values]
            for i, value in enumerate(cached):
                if value is not None:
                    results[i] = value

        pending = [
            (i, texts[i], keys[i])
            for i in range(len(texts))
            if cached[i] is None and texts[i].strip()
        ]
        if not pending:
            return results
        if self._breaker_open():
            logger.debug("translate_skipped_breaker_open: n=%d", len(pending))
            return results

        settings = get_settings()
        try:
            translated = await self._request(
                [t for _, t, _ in pending], source_lang, target
            )
        except TranslateUnavailable as exc:
            logger.warning("translate_failed: %s n=%d", exc, len(pending))
            self._record_failure(
                settings.translate_cb_fail_threshold,
                settings.translate_cb_cooldown_sec,
            )
            return results  # fallback: оригинальные тайтлы
        self._record_success()

        for (i, _text, key), ru in zip(pending, translated):
            if ru:
                results[i] = ru
                if self.redis is not None:
                    await self.redis.setex(key, self.cache_ttl, ru)
        return results


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
    )