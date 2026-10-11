"""RAGClient — обёртка над внешним rag_service (план внедрения RAG, фаза 3).

Обязательства из плана п. 4, фаза 3:
- retry с экспоненциальным backoff (сетевые ошибки; 4xx не ретраим);
- circuit breaker: N ошибок подряд → пауза cooldown (по умолчанию 5 / 5 мин);
- таймаут 5 сек;
- кэш ответов в Redis (TTL 1 час) по хэшу вопроса+context;
- Bearer-аутентификация (RAG_API_TOKEN).

Если RAG недоступен — `RAGUnavailable`, агент отвечает без контекста
(fallback обязателен, план п. 1.4). Если `rag_service_url` не задан —
клиент disabled, все вызовы сразу в fallback.
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from redis.asyncio import Redis

from app.schemas.rag import RAGAnswer, RAGAskRequest, RAGIngestResult

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "rag:answer"


class RAGUnavailable(RuntimeError):
    """RAG недоступен / circuit breaker открыт / ответ невалиден."""


class RAGClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        enabled: bool = True,
        timeout: int = 5,
        cache_ttl: int = 3600,
        redis: Redis | None = None,
        max_retries: int = 2,
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
            logger.warning("rag_circuit_breaker_open: cooldown=%ss", cooldown)

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = 0.0

    # --- headers / cache ---------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_token:
            h["Authorization"] = f"Bearer {self.api_token}"
        return h

    def _cache_key(self, question: str, context: dict[str, Any]) -> str:
        raw = json.dumps(
            {"q": question.strip().lower(), "ctx": context},
            sort_keys=True,
            ensure_ascii=False,
        )
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"{_CACHE_PREFIX}:{digest}"

    async def _cache_get(self, key: str) -> RAGAnswer | None:
        if self.redis is None:
            return None
        raw = await self.redis.get(key)
        if raw is None:
            return None
        try:
            answer = RAGAnswer.model_validate_json(raw)
            answer.cached = True
            return answer
        except Exception:  # noqa: BLE001 — битый кэш не ломает запрос
            return None

    async def _cache_set(self, key: str, answer: RAGAnswer) -> None:
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
        fail_threshold: int = 5,
        cooldown_sec: int = 300,
    ) -> RAGAnswer:
        """Вопрос → RAG Service POST /api/v1/ask. Кэш → HTTP (retry + breaker).

        Бросает RAGUnavailable: клиент выключен, breaker открыт,
        сеть/таймаут/HTTP-ошибка, невалидный ответ.
        """
        if not self.enabled:
            raise RAGUnavailable("rag_disabled")
        if self._breaker_open():
            raise RAGUnavailable("circuit_breaker_open")

        ctx = context or {}
        cache_key = self._cache_key(question, ctx)
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
                if resp.status_code >= 400:
                    reason = f"rag_http_{resp.status_code}"
                    raise RAGUnavailable(reason)
                answer = RAGAnswer.model_validate(resp.json())
                answer.latency_ms = int((time.perf_counter() - started) * 1000)
                self._record_success()
                await self._cache_set(cache_key, answer)
                logger.info(
                    "rag_ask: latency_ms=%d confidence=%.2f sources=%d",
                    answer.latency_ms, answer.confidence, len(answer.sources),
                )
                return answer
            except RAGUnavailable:
                last_error = None
                break  # HTTP-ошибка сервиса финальна — не ретраим
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                reason = f"rag_network_error: {exc}"
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * 2**attempt)  # backoff 0.5s, 1s

        self._record_failure(fail_threshold, cooldown_sec)
        if last_error is not None:
            raise RAGUnavailable(reason) from last_error
        raise RAGUnavailable(reason)

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
        """multipart-загрузка документа в базу знаний (план п. 6.4)."""
        if not self.enabled:
            raise RAGUnavailable("rag_disabled")
        path = Path(file_path)
        headers = {k: v for k, v in self._headers().items() if k != "Content-Type"}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout * 4, transport=self._transport  # файлы дольше
            ) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v1/ingest",
                    files={"file": (path.name, path.read_bytes())},
                    data={"metadata": json.dumps(metadata, ensure_ascii=False)},
                    headers=headers,
                )
            if resp.status_code >= 400:
                raise RAGUnavailable(f"rag_http_{resp.status_code}")
            return RAGIngestResult.model_validate(resp.json())
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RAGUnavailable(f"rag_network_error: {exc}") from exc


def rag_client_from_settings(redis: Redis | None = None) -> RAGClient:
    """Сборка клиента из настроек (для API-процесса и бота)."""
    from app.core.config import get_settings

    s = get_settings()
    return RAGClient(
        base_url=s.rag_service_url,
        api_token=s.rag_api_token,
        enabled=s.rag_enabled,
        timeout=s.rag_timeout_sec,
        cache_ttl=s.rag_cache_ttl_sec,
        redis=redis,
    )
