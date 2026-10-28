"""Прикладные ошибки: единственный язык ошибок для transport.

Домен бросает свои исключения; use case переводит их в `AppError`, чтобы
транспорт не импортировал домен.
"""

from __future__ import annotations


class AppError(Exception):
    """Базовая ошибка прикладного слоя."""

    code = "application_error"
    default_message = "Request cannot be processed"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)

    @property
    def public_message(self) -> str:
        """Безопасный текст для клиента."""
        return self.message


class NotFound(AppError):
    """Ресурс не найден или недоступен субъекту."""

    code = "not_found"
    default_message = "Resource not found"


class Forbidden(AppError):
    """Действие запрещено."""

    code = "forbidden"
    default_message = "Action is not allowed"


class Conflict(AppError):
    """Конфликт состояния."""

    code = "conflict"
    default_message = "State conflict"


class PaymentRequired(AppError):
    """Действие требует оплаты (не хватает баланса и т.п.)."""

    code = "payment_required"
    default_message = "Payment required"


class InvalidInput(AppError):
    """Некорректный ввод, не пойманный схемой."""

    code = "invalid_input"
    default_message = "Invalid input"
