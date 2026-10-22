"""Сценарий перевода тайтлов: батч через порт, breaker, fallback на оригинал.

Перевод НЕкритичен: при недоступности сервиса возвращаются `None` в позициях,
UI показывает оригинальный заголовок. Circuit breaker (в памяти процесса)
не даёт долбить лежащий сервис: N ошибок подряд → пауза cooldown_sec.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from app.application.catalog.ports import TranslationPort
from app.domain.translation.errors import TranslationUnavailable

logger = logging.getLogger(__name__)


class TranslateTexts:
    """Перевести батч текстов, не роняя вызывающий сценарий."""

    def __init__(
        self,
        translator: TranslationPort,
        *,
        enabled: bool = True,
        fail_threshold: int = 3,
        cooldown_sec: int = 300,
        on_open: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            translator: Порт перевода (HTTP + кэш).
            enabled: Выключенный перевод сразу отдаёт `None` без запросов.
            fail_threshold: Сколько ошибок подряд открывают breaker.
            cooldown_sec: Пауза, на которую breaker открывается.
            on_open: Уведомление об открытии (алерт), вызывается один раз.
            clock: Монотонное время (тесты).
        """
        self._translator = translator
        self._enabled = enabled
        self._fail_threshold = fail_threshold
        self._cooldown_sec = cooldown_sec
        self._on_open = on_open
        self._clock = clock
        self._consecutive_failures = 0
        self._open_until = 0.0

    def breaker_open(self) -> bool:
        return self._clock() < self._open_until

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = 0.0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures < self._fail_threshold:
            return
        self._open_until = self._clock() + self._cooldown_sec
        self._consecutive_failures = 0
        logger.warning("translate_circuit_breaker_open: cooldown=%ss", self._cooldown_sec)
        if self._on_open is not None:
            self._on_open()

    async def execute(
        self,
        texts: list[str],
        source_lang: str = "auto",
        target_lang: str = "rus_Cyrl",
    ) -> list[str | None]:
        """Перевести батч; ошибки порта → `None` (показываем оригинал)."""
        results: list[str | None] = [None] * len(texts)
        if not self._enabled or not texts:
            return results
        if self.breaker_open():
            logger.debug("translate_skipped_breaker_open: n=%d", len(texts))
            return results

        try:
            translated = await self._translator.translate_batch(
                texts, source_lang, target_lang
            )
        except TranslationUnavailable as exc:
            logger.warning("translate_failed: %s n=%d", exc, len(texts))
            self._record_failure()
            return results
        self._record_success()
        for i, value in enumerate(translated[: len(texts)]):
            results[i] = value or None
        return results
