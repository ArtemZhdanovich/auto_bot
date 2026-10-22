"""Ошибки админ-сценариев: транспорт маппит их в HTTP-коды.

Живут отдельно от use case-ов, чтобы инфраструктура (репозитории, хранилища)
импортировала их, не завися от сценариев.
"""

from __future__ import annotations


class AdminError(Exception):
    """Базовая ошибка админ-сценария."""


class InvalidSourceConfig(AdminError, ValueError):
    """Некорректный конфиг источника (422)."""


class SourceNotFound(AdminError, LookupError):
    """Источник (или его здоровье) не найден (404)."""


class SourceConflict(AdminError, RuntimeError):
    """Конкурентное изменение источника (409)."""


class QueueUnavailable(AdminError, RuntimeError):
    """Очередь задач недоступна (503)."""


class LLMUnavailable(AdminError, RuntimeError):
    """LLM не сконфигурирована или не ответила (503)."""


class RawPayloadNotFound(AdminError, LookupError):
    """Сырого payload в хранилище нет (404)."""


class StorageUnavailable(AdminError, RuntimeError):
    """Object storage не отдал presigned URL (502)."""
