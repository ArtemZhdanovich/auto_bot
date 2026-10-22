"""Базовые доменные ошибки.

Домен не знает про HTTP: transport сам отображает эти исключения в коды
ответа (см. `app/transport/http/errors.py` в blueprint).
"""

from __future__ import annotations


class DomainError(Exception):
    """Базовая ошибка домена.

    Attributes:
        code: Машиночитаемый код для клиента.
        message: Безопасное для клиента сообщение (без внутренних деталей).
    """

    code = "domain_error"
    default_message = "Request cannot be processed"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)

    @property
    def public_message(self) -> str:
        """Текст, который можно отдать клиенту."""
        return self.message


class NotFound(DomainError):
    """Ресурс не найден или недоступен текущему субъекту."""

    code = "not_found"
    default_message = "Resource not found"


class Forbidden(DomainError):
    """Действие запрещено для текущего субъекта."""

    code = "forbidden"
    default_message = "Action is not allowed"


class Conflict(DomainError):
    """Конфликт состояния (дубль, гонка, нарушение инварианта)."""

    code = "conflict"
    default_message = "State conflict"
