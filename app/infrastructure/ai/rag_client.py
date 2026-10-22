"""RAGClient — обёртка над внешним rag_service (план внедрения RAG, фаза 3).

Обязательства из плана п. 4, фаза 3:
- retry с экспоненциальным backoff (сетевые ошибки и 5xx; 4xx не ретраим);
- circuit breaker: N ошибок подряд (5xx/сеть) → пауза cooldown (по умолчанию 5 / 5 мин);
- таймаут 5 сек;
- кэш ответов в Redis (TTL 1 час) по хэшу вопроса+context;
- Bearer-аутентификация (RAG_API_TOKEN).

Если RAG недоступен — `RAGUnavailable`, агент отвечает без контекста
(fallback обязателен, план п. 1.4). Если `rag_service_url` не задан —
клиент disabled, все вызовы сразу в fallback.

Circuit breaker — **in-memory, per-process**: в multi-worker развёртывании
у каждого процесса свой. Состояние (fail count, cooldown) не разделяется.

Кэш не инвалидируется на `ingest`: после загрузки нового документа
ответы могут быть устаревшими до истечения TTL.
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr
from redis.asyncio import Redis

from app.application.dialog.dto import (
    RAGAnswer,
    RAGAskRequest,
    RAGIngestResult,
)
from app.infrastructure.services.alerting import fire, send_alert

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "rag:answer"
# 5xx и сетевые ошибки — транзиентные; 4xx — клиентские (не ретраим, breaker не трогаем).
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_DEFAULT_FAIL_THRESHOLD = 5
_DEFAULT_COOLDOWN_SEC = 300
_DEFAULT_MAX_INGEST_BYTES = 50 * 1024 * 1024  # 50 MB


class RAGUnavailable(RuntimeError):
    """RAG недоступен / circuit breaker открыт / ответ невалиден."""


def _secret_or_none(value: SecretStr | str | None) -> str | None:
    """Разворачивает `SecretStr` в `str`; `None` для пустого значения.

    После миграции config.py на `SecretStr` прямая передача в заголовок
    даёт `Bearer **********`.

    Args:
        value: `SecretStr`, `str` или `None`.

    Returns:
        Строка секрета или `None`, если значение пустое.
    """
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value.get_secret_value() or None
    return value or None


class RAGClient:
    """Клиент RAG-сервиса с retry, circuit breaker и кэшем.

    Attributes:
        enabled: `False`, если `rag_service_url` не задан или `rag_enabled=False`.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | SecretStr | None = None,
        enabled: bool = True,
        timeout: int = 5,
        cache_ttl: int = 3600,
        redis: Redis | None = None,
        max_retries: int = 2,
        fail_threshold: int = _DEFAULT_FAIL_THRESHOLD,
        cooldown_sec: int = _DEFAULT_COOLDOWN_SEC,
        max_ingest_bytes: int = _DEFAULT_MAX_INGEST_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        stripped = (base_url or "").strip().rstrip("/")
        self.base_url = stripped
        self.api_token = _secret_or_none(api_token)
        self.enabled = bool(enabled and stripped)
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.redis = redis
        self._max_retries = max_retries
        self._fail_threshold = fail_threshold
        self._cooldown_sec = cooldown_sec
        self._max_ingest_bytes = max_ingest_bytes
        self._transport = transport
        # Circuit breaker (in-memory, per-process).
        self._consecutive_failures = 0
        self._open_until = 0.0

    # --- circuit breaker -------------------------------------------------

    def _breaker_open(self) -> bool:
        """`True`, если breaker открыт (cooldown ещё не истёк).

        Полуоткрытое состояние не реализовано: после cooldown все
        запросы проходят одновременно (thundering herd). Для RAG с малым
        параллелизмом это приемлемо.
        """
        return time.monotonic() < self._open_until

    def _record_failure(self) -> None:
        """Регистрирует транзиентный сбой; при пороге — открывает breaker."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._fail_threshold:
            self._open_until = time.monotonic() + self._cooldown_sec
            self._consecutive_failures = 0
            logger.warning(
                "rag_circuit_breaker_open: cooldown=%ss threshold=%s",
                self._cooldown_sec, self._fail_threshold,
            )
            fire(
                send_alert(
                    f"🔴 RAG circuit breaker открыт на {self._cooldown_sec}s "
                    f"({self._fail_threshold} ошибок подряд) — ответы через LLM.",
                    dedup_key="cb:rag",
                )
            )

    def _record_success(self) -> None:
        """Сбрасывает счётчик сбоев и закрывает breaker."""
        self._consecutive_failures = 0
        self._open_until = 0.0

    # --- headers / cache ---------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Заголовки запроса: Content-Type + Authorization (если задан)."""
        h = {"Content-Type": "application/json"}
        if self.api_token:
            h["Authorization"] = f"Bearer {self.api_token}"
        return h

    def _cache_key(self, question: str, context: dict[str, Any]) -> str | None:
        """Хэш вопроса+context для Redis-кэша; `None` при несериализуемом context.

        Вопрос lowercased намеренно: «What is X?» и «what is x?» — один
        семантический запрос, кэшируем один раз. Для case-sensitive NER
        это может быть неверно; при необходимости — убрать `.lower()`.

        Args:
            question: Вопрос.
            context: Контекст (должен быть JSON-сериализуемым).

        Returns:
            Ключ кэша или `None`, если context не сериализуется (тогда
            кэш для этого запроса отключается).
        """
        try:
            raw = json.dumps(
                {"q": question.strip().lower(), "ctx": context},
                sort_keys=True,
                ensure_ascii=False,
            )
        except (TypeError, ValueError):
            logger.warning("rag_cache_key_not_serializable")
            return None
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"{_CACHE_PREFIX}:{digest}"

    async def _cache_get(self, key: str) -> RAGAnswer | None:
        """Читает ответ из кэша; битый кэш → `None` (без падения)."""
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(key)
        except Exception:  # noqa: BLE001 — кэш не критичен
            logger.warning("rag_cache_get_failed", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            answer = RAGAnswer.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — битый кэш не ломает запрос
            logger.warning("rag_cache_parse_error", exc_info=True)
            return None
        try:
            answer.cached = True
            # Кэш-хит не должен показывать latency исходного запроса.
            answer.latency_ms = 0
        except Exception:  # noqa: BLE001 — модель может быть frozen
            logger.debug("rag_cache_cached_flag_failed")
        return answer

    async def _cache_set(self, key: str, answer: RAGAnswer) -> None:
        """Пишет ответ в кэш; ошибки Redis не критичны."""
        if self.redis is None:
            return
        try:
            await self.redis.setex(key, self.cache_ttl, answer.model_dump_json())
        except Exception:  # noqa: BLE001 — кэш не критичен
            logger.warning("rag_cache_set_failed", exc_info=True)

    # --- ask -------------------------------------------------------------

    async def ask(
        self,
        question: str,
        context: dict[str, Any] | None = None,
        *,
        fail_threshold: int | None = None,
        cooldown_sec: int | None = None,
    ) -> RAGAnswer:
        """Вопрос → RAG Service POST /api/v1/ask. Кэш → HTTP (retry + breaker).

        Ретраятся сетевые ошибки и 5xx. 4xx — финальная клиентская ошибка,
        breaker не трогает (иначе misconfig маскируется под «сервис лежит»).

        Args:
            question: Вопрос.
            context: Контекст (`tenant`, `user_id`, `filters`).
            fail_threshold: Переопределение порога breaker (для совместимости).
            cooldown_sec: Переопределение cooldown (для совместимости).

        Returns:
            `RAGAnswer`.

        Raises:
            RAGUnavailable: Клиент выключен, breaker открыт, сеть/таймаут,
                HTTP-ошибка, невалидный ответ.
        """
        if not self.enabled:
            raise RAGUnavailable("rag_disabled")
        if self._breaker_open():
            raise RAGUnavailable("circuit_breaker_open")

        # Переопределения (обратная совместимость с сигнатурой ask(...)).
        effective_threshold = fail_threshold or self._fail_threshold
        effective_cooldown = cooldown_sec or self._cooldown_sec

        ctx = context or {}
        cache_key = self._cache_key(question, ctx)
        if cache_key is not None:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cached

        payload = RAGAskRequest(question=question, context=ctx).model_dump()
        started = time.perf_counter()
        last_error: Exception | None = None
        reason = "rag_bad_response"

        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, transport=self._transport
                ) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/v1/ask",
                        json=payload,
                        headers=self._headers(),
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                reason = f"rag_network_error: {exc}"
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * 2**attempt)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                reason = f"rag_http_{resp.status_code}"
                if attempt < self._max_retries:
                    logger.warning(
                        "rag_retryable_status status=%d attempt=%d/%d",
                        resp.status_code, attempt + 1, self._max_retries,
                    )
                    await asyncio.sleep(0.5 * 2**attempt)
                continue

            if resp.status_code >= 400:
                # 4xx — клиентская ошибка: не ретраим, breaker не трогаем.
                logger.warning(
                    "rag_client_error status=%d (not counted toward breaker)",
                    resp.status_code,
                )
                raise RAGUnavailable(f"rag_http_{resp.status_code}")

            # 2xx
            try:
                answer = RAGAnswer.model_validate(resp.json())
            except (ValueError, TypeError) as exc:
                # Невалидный ответ — сервер сломан, считаем как failure.
                last_error = exc
                reason = "rag_bad_response"
                logger.warning(
                    "rag_invalid_response: %r (body=%r)",
                    exc, resp.text[:200],
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * 2**attempt)
                continue

            answer.latency_ms = int((time.perf_counter() - started) * 1000)
            self._record_success()
            if cache_key is not None:
                # Обнуляем latency_ms перед кэшированием: при кэш-хите
                # значение будет «свежим», а не оригинальным.
                cacheable = answer.model_copy(update={"latency_ms": 0})
                await self._cache_set(cache_key, cacheable)
            confidence = (
                f"{answer.confidence:.2f}"
                if answer.confidence is not None
                else "n/a"
            )
            logger.info(
                "rag_ask: latency_ms=%d confidence=%s sources=%d",
                answer.latency_ms, confidence, len(answer.sources),
            )
            return answer

        # Все ретраи исчерпаны — это транзиентный сбой, считаем.
        self._record_failure_with(effective_threshold, effective_cooldown)
        if last_error is not None:
            raise RAGUnavailable(reason) from last_error
        raise RAGUnavailable(reason)

    def _record_failure_with(self, threshold: int, cooldown: int) -> None:
        """Версия `_record_failure` с явными порогами (для переопределений в `ask`)."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= threshold:
            self._open_until = time.monotonic() + cooldown
            self._consecutive_failures = 0
            logger.warning(
                "rag_circuit_breaker_open: cooldown=%ss threshold=%s",
                cooldown, threshold,
            )
            fire(
                send_alert(
                    f"🔴 RAG circuit breaker открыт на {cooldown}s "
                    f"({threshold} ошибок подряд) — ответы через LLM.",
                    dedup_key="cb:rag",
                )
            )

    # --- healthcheck / ingest ---------------------------------------------

    async def healthcheck(self) -> bool:
        """GET /health. Для мониторинга; circuit breaker не трогает."""
        if not self.enabled:
            return False
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                resp = await client.get(f"{self.base_url}/health")
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def ingest(
        self, file_path: str | Path, metadata: dict[str, Any]
    ) -> RAGIngestResult:
        """multipart-загрузка документа в базу знаний (план п. 6.4).

        Retry-политика и circuit breaker — как у `ask` (сетевые и 5xx),
        но счётчик сбоев ingest **не** влияет на breaker `ask` (разные
        пути нагрузки; см. комментарий в `_record_failure_with`).

        Args:
            file_path: Путь к файлу.
            metadata: Метаданные (JSON-сериализуемые).

        Returns:
            `RAGIngestResult`.

        Raises:
            RAGUnavailable: Клиент выключен, файл слишком большой,
                сеть/таймаут, HTTP-ошибка или невалидный ответ.
        """
        if not self.enabled:
            raise RAGUnavailable("rag_disabled")
        path = Path(file_path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise RAGUnavailable(f"rag_ingest_file_error: {exc}") from exc
        if size > self._max_ingest_bytes:
            raise RAGUnavailable(
                f"rag_ingest_too_large: {size} > {self._max_ingest_bytes}"
            )

        try:
            file_bytes = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise RAGUnavailable(f"rag_ingest_file_error: {exc}") from exc

        try:
            metadata_json = json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise RAGUnavailable(f"rag_ingest_metadata_error: {exc}") from exc

        headers = {k: v for k, v in self._headers().items() if k != "Content-Type"}

        last_error: Exception | None = None
        reason = "rag_bad_response"
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout * 4,  # файлы дольше
                    transport=self._transport,
                ) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/v1/ingest",
                        files={"file": (path.name, file_bytes)},
                        data={"metadata": metadata_json},
                        headers=headers,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                reason = f"rag_network_error: {exc}"
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * 2**attempt)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                reason = f"rag_http_{resp.status_code}"
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * 2**attempt)
                continue

            if resp.status_code >= 400:
                raise RAGUnavailable(f"rag_http_{resp.status_code}")

            try:
                return RAGIngestResult.model_validate(resp.json())
            except (ValueError, TypeError) as exc:
                last_error = exc
                reason = "rag_bad_response"
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * 2**attempt)
                continue

        if last_error is not None:
            raise RAGUnavailable(reason) from last_error
        raise RAGUnavailable(reason)


def rag_client_from_settings(redis: Redis | None = None) -> RAGClient:
    """Сборка клиента из настроек (для API-процесса и бота).

    Args:
        redis: Клиент Redis для кэша (опционально).

    Returns:
        Настроенный `RAGClient`.
    """
    from app.config import get_settings

    s = get_settings()
    return RAGClient(
        base_url=s.rag_service_url,
        api_token=s.rag_api_token,
        enabled=s.rag_enabled,
        timeout=s.rag_timeout_sec,
        cache_ttl=s.rag_cache_ttl_sec,
        redis=redis,
        fail_threshold=s.rag_cb_fail_threshold,
        cooldown_sec=s.rag_cb_cooldown_sec,
    )