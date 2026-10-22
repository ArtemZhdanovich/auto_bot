"""Ошибки контекста перевода."""

from __future__ import annotations

from app.domain.shared.errors import DomainError


class TranslationUnavailable(DomainError):
    """Сервис перевода недоступен, ответ невалиден или breaker открыт."""

    code = "translation_unavailable"
    default_message = "Translation service is unavailable"
