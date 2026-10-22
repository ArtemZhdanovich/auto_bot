"""Сценарии авторизации: initData, веб-пароли, одноразовые login-коды.

Слой знает только порты (`Protocol`), поэтому ни SQL, ни Redis, ни FastAPI
здесь нет. Адаптеры: `infrastructure/db/repositories/users_auth.py`
(пользователи) и `infrastructure/security/login_codes.py` (коды, JWT,
initData, PBKDF2).

================================================================================
ПОЧЕМУ КОД ВХОДА ПРИВЯЗАН К БРАУЗЕРУ (SESSION FIXATION)
================================================================================

Атака «инициатор ≠ подтверждающий»:

1. Атакующий вызывает `POST /telegram/link` в своём браузере — получает `code`
   и HttpOnly-cookie `tglogin_state` (хеш state привязан к его браузеру).
2. Отправляет жертве ссылку `https://t.me/<bot>?start=login_<code>`.
3. Жертва подтверждает код в боте.
4. Атакующий поллит `GET /telegram/link/{code}` со своей cookie и получает JWT
   жертвы.

Защита состоит из двух независимых частей:

1. `state_hash` — SHA-256 от cookie инициатора. Без совпадения cookie обмен
   кода отвечает 401: чужой браузер код не заберёт.
2. Контекст инициатора (`initiator_ip`, `initiator_ua`, `created_at`) отдаётся
   боту через internal `GET /telegram/link/{code}/context`. Бот ОБЯЗАН показать
   его пользователю и вызвать `confirm` только после явного «✅ Это я», а при
   «❌ Отмена» — `DELETE /telegram/link/{code}`.

Одной cookie мало: `state_hash` привязан к инициатору, а `telegram_id` —
к подтверждающему. Поэтому UX-подтверждение в боте — часть контракта, а не
«улучшение»: API не может запретить боту врать и подтверждать код молча.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time

from app.application.analytics.use_cases import TrackEvent
from app.application.identity.dto import (
    LOGIN_EXPIRED,
    LOGIN_OK,
    LOGIN_PENDING,
    AuthResult,
    ConfirmLoginCommand,
    CurrentUser,
    LoginCommand,
    LoginContext,
    LoginLink,
    LoginPollResult,
    RegisterCommand,
    TokenPair,
    UserProfile,
)
from app.application.identity.ports import (
    InitDataVerifier,
    LoginCodeStore,
    PasswordHasher,
    TokenIssuer,
    UserRepository,
)
# Ошибки сценариев живут в нейтральном модуле контекста (errors), чтобы
# инфраструктура (реализации портов) не зависела от сценариев.
from app.application.auth.errors import (  # noqa: F401 — ре-экспорт
    AuthError,
    BotNotConfigured,
    DuplicateUserId,
    EmailConflict,
    InvalidCredentials,
    InvalidEmail,
    InvalidInitData,
    InvalidToken,
    LoginLinkExpired,
    LoginStateMismatch,
    UserDeactivated,
    UserIdAllocationFailed,
    UserNotFound,
)
from app.domain.analytics.events import EVENT_USER_REGISTERED

logger = logging.getLogger(__name__)

_LOGIN_CODE_TTL_SEC = 900      # жизнь одноразового кода входа
_LOGIN_CONFIRM_TTL_SEC = 900   # сколько времени веб может забрать JWT после /start
_UA_MAX_LEN = 512              # cap на длину User-Agent в Redis
_ID_ATTEMPTS = 5               # попытки выделить синтетический telegram_id

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EMAIL_MAX_LEN = 254  # RFC 5321


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _hash_state(state: str) -> str:
    """SHA-256 от state-cookie: в Redis лежит только хеш, не raw."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _normalize_email(email: str) -> str:
    """Нормализовать и проверить email.

    Raises:
        InvalidEmail: пустой, слишком длинный или неверного формата.
    """
    normalized = email.strip().lower()
    if len(normalized) > _EMAIL_MAX_LEN or not _EMAIL_RE.match(normalized):
        raise InvalidEmail
    return normalized


def _synthetic_telegram_id() -> int:
    """Синтетический PK для веб-пользователей: отрицательный 10-значный id.

    `telegram_id` остаётся PK/FK для всех таблиц, поэтому веб-аккаунту выдаём
    уникальный отрицательный идентификатор (не пересекается с настоящими
    Telegram id). `secrets.randbelow` — без модуло-биаса.
    """
    return -(secrets.randbelow(9_000_000_000) + 1_000_000_000)


def _auth_result(profile: UserProfile, tokens: TokenIssuer) -> AuthResult:
    """Собрать результат аутентификации из профиля и выпущенных токенов."""
    return AuthResult(tokens=tokens.issue(profile.telegram_id), user=profile)


# ---------------------------------------------------------------------------
# Mini App / Telegram
# ---------------------------------------------------------------------------


class AuthenticateTelegram:
    """Вход по initData из Telegram Mini App."""

    def __init__(
        self,
        *,
        users: UserRepository,
        init_data: InitDataVerifier,
        tokens: TokenIssuer,
        track: TrackEvent,
    ) -> None:
        self._users = users
        self._init_data = init_data
        self._tokens = tokens
        self._track = track

    async def execute(self, init_data: str) -> AuthResult:
        """Проверить initData, обновить профиль и выдать JWT.

        Args:
            init_data: Строка `Telegram.WebApp.initData`.

        Returns:
            Токены и профиль.

        Raises:
            InvalidInitData: Подпись/срок не в порядке.
            BotNotConfigured: Не задан `BOT_TOKEN`.
            UserDeactivated: Пользователь забанен (403).
        """
        identity = self._init_data.verify(init_data)
        outcome = await self._users.upsert(
            telegram_id=identity.telegram_id,
            username=identity.username,
            first_name=identity.first_name,
            last_name=identity.last_name,
            language_code=identity.language_code,
        )
        profile = outcome.profile
        if not profile.is_active:
            raise UserDeactivated
        if outcome.created:  # воронка: событие только при первой регистрации
            await self._track.execute(
                profile.telegram_id, EVENT_USER_REGISTERED, {"source": "miniapp"}
            )
        return _auth_result(profile, self._tokens)


class CreateTelegramLoginLink:
    """Одноразовый код входа + state для привязки к браузеру."""

    def __init__(self, *, codes: LoginCodeStore) -> None:
        self._codes = codes

    async def execute(
        self, *, initiator_ip: str | None, initiator_ua: str
    ) -> LoginLink:
        """Создать код в статусе `pending`.

        Args:
            initiator_ip: IP инициатора (может быть за прокси без XFF — None).
            initiator_ua: User-Agent инициатора (может быть пустым).

        Returns:
            Код, raw `state` (только в cookie) и TTL.
        """
        code = secrets.token_urlsafe(12)
        state = secrets.token_urlsafe(32)
        await self._codes.save_pending(
            code,
            state_hash=_hash_state(state),
            initiator_ip=initiator_ip,
            initiator_ua=initiator_ua[:_UA_MAX_LEN],
            created_at=int(time.time()),
            ttl_sec=_LOGIN_CODE_TTL_SEC,
        )
        logger.info(
            "telegram_login_link_created code=%s ip=%s", code, initiator_ip
        )
        return LoginLink(
            code=code, state=state, expires_in=_LOGIN_CODE_TTL_SEC
        )


class PollTelegramLogin:
    """Статус входа: pending / expired / JWT-пара (одноразово)."""

    def __init__(
        self,
        *,
        codes: LoginCodeStore,
        users: UserRepository,
        tokens: TokenIssuer,
        track: TrackEvent,
    ) -> None:
        self._codes = codes
        self._users = users
        self._tokens = tokens
        self._track = track

    async def execute(self, code: str, state: str | None) -> LoginPollResult:
        """Обменять подтверждённый код на токены.

        Sliding TTL: каждый pending-poll продлевает код — пользователь может
        медлить в боте, «код истёк, начните заново» не нужен.

        Args:
            code: Одноразовый код из `POST /telegram/link`.
            state: Значение cookie `tglogin_state` (None — cookie нет).

        Returns:
            `pending`, `expired` или готовая пара токенов.

        Raises:
            LoginStateMismatch: Cookie отсутствует или не совпадает (401).
            UserDeactivated: Пользователь забанен (403).
        """
        record = await self._codes.load(code)
        if record is None:
            return LoginPollResult(status=LOGIN_EXPIRED)
        if (
            not state
            or not record.state_hash
            or not hmac.compare_digest(_hash_state(state), record.state_hash)
        ):
            raise LoginStateMismatch
        if record.status != LOGIN_OK:
            await self._codes.touch(code, _LOGIN_CODE_TTL_SEC)
            return LoginPollResult(status=LOGIN_PENDING)

        claimed = await self._codes.claim(code)
        if claimed is None or claimed.telegram_id is None:
            return LoginPollResult(status=LOGIN_EXPIRED)

        outcome = await self._users.upsert(
            telegram_id=claimed.telegram_id,
            username=claimed.username,
            first_name=claimed.first_name,
            last_name=claimed.last_name,
            language_code=claimed.language_code,
        )
        profile = outcome.profile
        if not profile.is_active:
            raise UserDeactivated
        if outcome.created:
            await self._track.execute(
                profile.telegram_id,
                EVENT_USER_REGISTERED,
                {"source": "telegram_link"},
            )
        logger.info(
            "telegram_login_completed code=%s telegram_id=%s",
            code,
            profile.telegram_id,
        )
        return LoginPollResult(
            status=LOGIN_OK, auth=_auth_result(profile, self._tokens)
        )


class GetLoginContext:
    """Контекст инициатора для бота (UX-фикс session fixation)."""

    def __init__(self, *, codes: LoginCodeStore) -> None:
        self._codes = codes

    async def execute(self, code: str) -> LoginContext:
        """Вернуть IP/UA/время инициатора.

        Raises:
            LoginLinkExpired: Кода нет или он повреждён (404).
        """
        record = await self._codes.load(code)
        if record is None:
            raise LoginLinkExpired
        return LoginContext(
            code=code,
            status=record.status,
            initiator_ip=record.initiator_ip,
            initiator_ua=record.initiator_ua,
            created_at=record.created_at,
        )


class CancelTelegramLogin:
    """Инвалидация кода: жертва нажала «❌ Отмена»."""

    def __init__(self, *, codes: LoginCodeStore) -> None:
        self._codes = codes

    async def execute(self, code: str) -> None:
        """Удалить код.

        Raises:
            LoginLinkExpired: Кода уже нет (404).
        """
        if not await self._codes.delete(code):
            raise LoginLinkExpired
        logger.warning("telegram_login_cancelled code=%s", code)


class ConfirmTelegramLogin:
    """Бот подтвердил `/start login_<code>`."""

    def __init__(self, *, codes: LoginCodeStore) -> None:
        self._codes = codes

    async def execute(self, command: ConfirmLoginCommand) -> None:
        """Пометить код подтверждённым, сохранив state_hash и контекст.

        Raises:
            LoginLinkExpired: Кода нет (404).
        """
        record = await self._codes.confirm(
            command.code,
            telegram_id=command.telegram_id,
            username=command.username,
            first_name=command.first_name,
            last_name=command.last_name,
            language_code=command.language_code,
            ttl_sec=_LOGIN_CONFIRM_TTL_SEC,
        )
        if record is None:
            raise LoginLinkExpired
        if (
            command.acknowledged_at is not None
            and record.created_at is not None
            and command.acknowledged_at < record.created_at
        ):
            logger.warning(
                "telegram_login_suspicious_ack code=%s ack=%d created=%d",
                command.code,
                command.acknowledged_at,
                record.created_at,
            )
        logger.info(
            "telegram_login_confirmed code=%s telegram_id=%s ack=%s",
            command.code,
            command.telegram_id,
            command.acknowledged_at,
        )


# ---------------------------------------------------------------------------
# Сессии
# ---------------------------------------------------------------------------


class RefreshSession:
    """Обмен refresh-токена на новую пару access+refresh."""

    def __init__(self, *, users: UserRepository, tokens: TokenIssuer) -> None:
        self._users = users
        self._tokens = tokens

    async def execute(self, refresh_token: str) -> TokenPair:
        """Проверить refresh и выпустить новую пару.

        Ротация refresh (инвалидация старого) — отдельный TECHDEBT: нужен
        `users.refresh_token_version` и проверка в `decode_token`.

        Raises:
            InvalidToken: Токен не refresh/невалиден (401).
            UserNotFound: Пользователя нет или он деактивирован (401).
        """
        user_id = self._tokens.refresh_subject(refresh_token)
        profile = await self._users.find(user_id)
        if profile is None or not profile.is_active:
            raise UserNotFound
        return self._tokens.issue(profile.telegram_id)


# ---------------------------------------------------------------------------
# Веб-регистрация (сайт): поиск доступен только зарегистрированным
# ---------------------------------------------------------------------------


class RegisterWebUser:
    """Регистрация на сайте: email + пароль → JWT + профиль."""

    def __init__(
        self,
        *,
        users: UserRepository,
        passwords: PasswordHasher,
        tokens: TokenIssuer,
    ) -> None:
        self._users = users
        self._passwords = passwords
        self._tokens = tokens

    async def execute(self, command: RegisterCommand) -> AuthResult:
        """Создать веб-аккаунт и сразу выдать токены.

        Raises:
            InvalidEmail: Неверный формат email (422).
            EmailConflict: Email уже занят (409).
            UserIdAllocationFailed: Не удалось выделить id (500).
        """
        email = _normalize_email(command.email)
        if await self._users.find_by_email(email) is not None:
            raise EmailConflict

        # PBKDF2 один раз, вне retry-цикла: 240k итераций (~100ms) не должны
        # повторяться 5 раз при редкой коллизии ID.
        password_hash = await self._passwords.hash(command.password)

        for _ in range(_ID_ATTEMPTS):
            try:
                profile = await self._users.create(
                    telegram_id=_synthetic_telegram_id(),
                    email=email,
                    password_hash=password_hash,
                    first_name=command.first_name,
                )
            except DuplicateUserId:
                continue
            return _auth_result(profile, self._tokens)
        raise UserIdAllocationFailed


class CreateGuestSession:
    """Гостевой режим: сайт открыт без регистрации (демо функционала)."""

    def __init__(
        self, *, users: UserRepository, tokens: TokenIssuer
    ) -> None:
        self._users = users
        self._tokens = tokens

    async def execute(self) -> AuthResult:
        """Создать гостя («Гость», id < 0, без email/пароля) и выдать JWT.

        Raises:
            UserIdAllocationFailed: Не удалось выделить id (500).
        """
        for _ in range(_ID_ATTEMPTS):
            try:
                profile = await self._users.create(
                    telegram_id=_synthetic_telegram_id(),
                    first_name="Гость",
                    role="buyer",
                    subscription_tier="free",
                )
            except DuplicateUserId:
                continue
            return _auth_result(profile, self._tokens)
        raise UserIdAllocationFailed


class LoginWebUser:
    """Вход на сайте: email + пароль."""

    def __init__(
        self,
        *,
        users: UserRepository,
        passwords: PasswordHasher,
        tokens: TokenIssuer,
    ) -> None:
        self._users = users
        self._passwords = passwords
        self._tokens = tokens

    async def execute(self, command: LoginCommand) -> AuthResult:
        """Проверить пароль и выдать токены.

        Тайминг выровнен для всех 401-путей: PBKDF2 выполняется всегда
        (адаптер подставляет dummy-хеш, если пользователя нет или хеша нет),
        поэтому нельзя отличить «email нет» от «email есть, пароль неверный».

        Raises:
            InvalidEmail: Неверный формат email (422).
            InvalidCredentials: Нет пользователя или пароль неверный (401).
            UserDeactivated: Пользователь забанен (403).
        """
        email = _normalize_email(command.email)
        account = await self._users.find_by_email(email)
        is_valid = await self._passwords.verify(
            command.password,
            account.password_hash if account is not None else None,
        )
        if account is None or not is_valid:
            raise InvalidCredentials
        if not account.profile.is_active:
            raise UserDeactivated
        return _auth_result(account.profile, self._tokens)


class GetCurrentUser:
    """Активный пользователь по `telegram_id` из access-токена (auth HTTP).

    Роутерам нужен только `telegram_id`: сценарии принимают id, профиль
    целиком отдаёт `GET /api/me`. Ошибки маппит транспорт:
    `UserNotFound` → 401 (токен на удалённого), `UserDeactivated` → 403
    (не 401, иначе клиент зациклит refresh).
    """

    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def execute(self, telegram_id: int) -> CurrentUser:
        """Загрузить профиль и проверить активность.

        Raises:
            UserNotFound: пользователя нет (удалён / отозван).
            UserDeactivated: пользователь забанен.
        """
        profile = await self._users.find(telegram_id)
        if profile is None:
            raise UserNotFound
        if not profile.is_active:
            raise UserDeactivated
        return CurrentUser(
            telegram_id=profile.telegram_id,
            role=profile.role,
            subscription_tier=profile.subscription_tier,
            subscription_until=profile.subscription_until,
        )
