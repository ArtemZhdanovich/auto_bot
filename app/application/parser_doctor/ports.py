"""Порты ParserDoctor: что сценарию нужно от источника, БД, LLM и Redis.

Ни `httpx`, ни SQLAlchemy, ни `redis` сюда не проникают — реализации живут
в инфраструктурном слое (`sources/probe.py`, `db/repositories`, `locks.py`).
"""

from __future__ import annotations

from typing import Any, Protocol

from app.application.dto.doctor import ListPage, SourceConfig


class SourceConfigRepository(Protocol):
    """Конфиг источника: чтение и запись отремонтированных селекторов."""

    async def get(self, source_name: str) -> SourceConfig | None:
        """Конфиг источника или `None`, если источника нет."""

    async def save_config(self, source_name: str, config: dict[str, Any]) -> None:
        """Записать новый конфиг в текущую транзакцию."""


class ParserProbe(Protocol):
    """Живая страница списка и проверка селекторов на её HTML."""

    async def fetch_list_page(self, config: dict[str, Any]) -> ListPage:
        """Забрать страницу списка тем же HTTP-клиентом, что и парсер."""

    def validate_selectors(self, config: dict[str, Any], html: str) -> int | None:
        """Спарсить HTML с селекторами: число карточек или `None`."""


class SourceRegistryPort(Protocol):
    """Перезагрузка адаптера источника после смены конфига."""

    async def reload(self, source_name: str) -> None:
        """Пересобрать адаптер из нового конфига."""


class AlertPort(Protocol):
    """Алерт владельцу (Telegram) с дедупликацией."""

    async def send(self, text: str, *, dedup_key: str) -> None:
        """Отправить текст; повтор с тем же ключом подавляется."""


class LockPort(Protocol):
    """Именованный лок с TTL (Redis `SET NX EX`)."""

    async def acquire(self, key: str, ttl_sec: int) -> bool:
        """True — ключ наш, False — уже занят/на cooldown."""

    async def release(self, key: str) -> None:
        """Снять ключ (идемпотентно)."""
