"""Ошибки сценариев авторизации: транспорт маппит их в 401/403/404/409/422/503.

Живут отдельно от use case-ов, чтобы инфраструктура (реализации портов)
импортировала их, не завися от сценариев.
"""

from __future__ import annotations

from app.application.errors import AppError


class AuthError(AppError):
    """Базовая ошибка авторизации."""

    code = "auth_error"
    default_message = "Authentication failed"


class InvalidCredentials(AuthError):
    """Неверная пара email/пароль."""

    code = "invalid_credentials"
    default_message = "Invalid credentials"


class InvalidToken(AuthError):
    """Токен не разбирается, просрочен или не того типа."""

    code = "invalid_token"
    default_message = "Invalid refresh token"


class UserNotFound(AuthError):
    """Валидный refresh, но пользователя нет или он деактивирован."""

    code = "user_not_found"
    default_message = "User not found"


class InvalidInitData(AuthError):
    """initData не прошёл проверку подписи/срока."""

    code = "invalid_init_data"
    default_message = "Invalid initData"


class LoginStateMismatch(AuthError):
    """Cookie браузера не совпадает с state_hash кода."""

    code = "login_state_mismatch"
    default_message = "Login state mismatch"


class UserDeactivated(AuthError):
    """Пользователь забанен."""

    code = "user_deactivated"
    default_message = "User is deactivated"


class LoginLinkExpired(AuthError):
    """Код входа не найден, истёк или уже использован."""

    code = "login_link_expired"
    default_message = "Login link expired"


class EmailConflict(AuthError):
    """Email уже занят другим аккаунтом."""

    code = "email_taken"
    default_message = "Email already registered"


class InvalidEmail(AuthError):
    """Email не проходит валидацию."""

    code = "invalid_email"
    default_message = "Invalid email"


class DuplicateUserId(AuthError):
    """Коллизия синтетического telegram_id — сигнал повторить попытку."""

    code = "duplicate_user_id"
    default_message = "User id collision"


class UserIdAllocationFailed(AuthError):
    """Не удалось выделить свободный синтетический telegram_id."""

    code = "user_id_allocation_failed"
    default_message = "Could not allocate user id"


class BotNotConfigured(AuthError):
    """BOT_TOKEN не задан — проверять initData нечем."""

    code = "bot_not_configured"
    default_message = "Bot token is not configured"
