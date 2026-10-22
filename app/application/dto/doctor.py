"""DTO ParserDoctor: конфиг источника, живая страница, итог ремонта."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """Конфиг источника (селекторы, base_url, режим врача)."""

    name: str
    config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ListPage:
    """Ответ страницы списка: код, HTML или сетевая ошибка."""

    status: int | None = None
    html: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """Итог работы врача; `to_payload` — контракт результата (dict)."""

    source: str
    status: str
    items: int | None = None
    proposed: dict[str, Any] | None = None
    http_status: int | None = None
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Собрать результат в том же виде, что отдавал сервис."""
        payload: dict[str, Any] = {"source": self.source, "status": self.status}
        if self.items is not None:
            payload["items"] = self.items
        if self.proposed is not None:
            payload["proposed"] = self.proposed
        if self.http_status is not None:
            payload["http_status"] = self.http_status
        if self.error:
            payload["error"] = self.error
        return payload
