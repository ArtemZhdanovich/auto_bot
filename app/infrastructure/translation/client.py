"""Адаптеры перевода: HTTP-клиент сервиса NLLB и Redis-кэш поверх `TranslationPort`."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import httpx

from app.application.shared.ports import CachePort
from app.application.catalog.ports import TranslationPort
from app.domain.translation.errors import TranslationUnavailable

logger = logging.getLogger(__name__)


def cache_key(text: str, source_lang: str, target_lang: str) -> str:
    """Ключ кэша перевода: `tr:{target}:{src}:{sha1(title)}`."""
    digest = hashlib.sha1(text.encode()).hexdigest()
    return f"tr:{target_lang}:{source_lang}:{digest}"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


class HttpTranslateClient:
    """Батчевый `POST /translate` с retry на сетевых ошибках.

    HTTP 4xx/5xx и невалидный ответ финальны — сразу `TranslationUnavailable`
    (перевод некритичен: вызывающий покажет оригинальный title).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    async def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str | None]:
        payload = {
            "texts": texts,
            "source_lang": source_lang,
            "target_lang": target_lang,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.post(
                        f"{self._base_url}/translate",
                        json=payload,
                        headers=self._headers(),
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.5 * 2**attempt)
                        continue
                    raise TranslationUnavailable(
                        f"translate_network_error: {exc}"
                    ) from exc
                if resp.status_code >= 400:
                    raise TranslationUnavailable(f"translate_http_{resp.status_code}")
                translations = resp.json().get("translations")
                if not isinstance(translations, list) or len(translations) != len(
                    texts
                ):
                    raise TranslationUnavailable("translate_bad_response")
                return translations
        raise TranslationUnavailable("translate_network_error: retries exhausted")


class CachedTranslationClient:
    """`TranslationPort` с кэшем: читает `tr:*`, переводит только промахи.

    Корейские/китайские заголовки повторяются массово — кэш экономит 90%+ CPU.
    """

    def __init__(
        self,
        client: TranslationPort,
        cache: CachePort,
        *,
        ttl_sec: int = 60 * 60 * 24 * 30,
    ) -> None:
        self._client = client
        self._cache = cache
        self._ttl = ttl_sec

    async def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str | None]:
        results: list[str | None] = [None] * len(texts)
        if not texts:
            return results

        keys = [cache_key(t, source_lang, target_lang) for t in texts]
        cached = [_text(value) for value in await self._cache.get_many(keys)]
        pending: list[int] = []
        for i, value in enumerate(cached):
            if value is not None:
                results[i] = value
            elif texts[i].strip():
                pending.append(i)
        if not pending:
            return results

        translated = await self._client.translate_batch(
            [texts[i] for i in pending], source_lang, target_lang
        )
        if len(translated) != len(pending):
            raise TranslationUnavailable("translate_bad_response")
        for i, value in zip(pending, translated):
            if value:
                results[i] = value
                await self._cache.set(keys[i], value, self._ttl)
        return results
