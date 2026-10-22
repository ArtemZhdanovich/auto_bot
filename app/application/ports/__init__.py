"""Порты внешних систем. Реализации живут в инфраструктурном слое."""

from __future__ import annotations

from typing import Protocol


class CachePort(Protocol):
    """Key-value кэш (Redis)."""

    async def get(self, key: str) -> bytes | str | None: ...

    async def get_many(self, keys: list[str]) -> list[bytes | str | None]: ...

    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None: ...

    async def set_if_absent(self, key: str, value: str, ttl_sec: int) -> bool: ...

    async def delete(self, key: str) -> None: ...

    async def incr(self, key: str) -> int: ...

    async def expire(self, key: str, ttl_sec: int) -> None: ...


class ObjectStoragePort(Protocol):
    """S3-совместимое хранилище."""

    async def put(
        self,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict | None = None,
    ) -> None: ...

    async def presign_get(self, key: str, ttl_sec: int) -> str: ...

    async def delete(self, key: str) -> None: ...


class LLMPort(Protocol):
    """Один вызов языковой модели."""

    async def complete(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> object: ...


class RAGPort(Protocol):
    """Вопрос к RAG-сервису."""

    async def ask(self, question: str, context: str | None = None) -> object: ...

    async def healthcheck(self) -> bool: ...


class NotifierPort(Protocol):
    """Отправка текста пользователю (Telegram)."""

    async def send_text(self, chat_id: int, text: str) -> bool: ...


class TranslationPort(Protocol):
    """Батчевый перевод текстов; `None` в позиции — текст не переведён."""

    async def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str | None]: ...


class QuotaStore(Protocol):
    """Счётчики квоты (Redis-совместимый инкремент + TTL)."""

    async def get_int(self, key: str) -> int: ...

    async def add(self, key: str, delta: int) -> int: ...

    async def expire(self, key: str, ttl_sec: int) -> None: ...


class ClockPort(Protocol):
    """Источник времени (детерминированные тесты)."""

    def now(self) -> object: ...
