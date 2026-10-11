"""Авторизация: initData из Mini App, веб-регистрация, magic-link (ТЗ п. 4.3, 9.5)."""

import hashlib
import hmac
import re
import secrets

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import SessionDep
from app.core.security import (
    InitDataError,
    create_access_token,
    create_refresh_token,
    validate_init_data,
)
from app.db.models import User
from app.schemas.user import (
    AuthResponse,
    MagicLinkRequest,
    TelegramAuthRequest,
    UserOut,
)
from app.services.users import upsert_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid email"
        )
    return email


async def _authenticate_via_init_data(
    session: AsyncSession, init_data: str
) -> User:
    settings = get_settings()
    if not settings.bot_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot token is not configured",
        )
    try:
        tg_user = validate_init_data(
            init_data=init_data,
            bot_token=settings.bot_token,
            max_age_seconds=settings.init_data_max_age_sec,
        )
    except InitDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    return await upsert_user(
        session,
        telegram_id=int(tg_user["id"]),
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
        last_name=tg_user.get("last_name"),
        language_code=tg_user.get("language_code"),
    )


@router.post("/telegram", response_model=AuthResponse)
async def auth_telegram(body: TelegramAuthRequest, session: SessionDep) -> AuthResponse:
    """Вход по initData из Mini App → JWT (access + refresh) + профиль."""
    user = await _authenticate_via_init_data(session, body.init_data)
    settings = get_settings()
    return AuthResponse(
        access_token=create_access_token(user.telegram_id, settings),
        refresh_token=create_refresh_token(user.telegram_id, settings),
        user=UserOut.model_validate(user),
    )


@router.post("/magic-link", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def auth_magic_link(body: MagicLinkRequest) -> dict[str, str]:
    """Вход по ссылке из бота. Реализуется на этапах 3–4 (ТЗ п. 14.3)."""
    return {"detail": "magic-link auth will be implemented at stage 3-4"}


# ---------- Веб-регистрация (сайт): поиск доступен только зарегистрированным ----------

_PBKDF2_ITERATIONS = 240_000


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


class WebRegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    first_name: str | None = Field(default=None, max_length=64)


class WebLoginRequest(BaseModel):
    email: str
    password: str


def _synthetic_telegram_id() -> int:
    """Синтетический PK для веб-пользователей: отрицательный 10-значный id.

    telegram_id остаётся PK/FK для всех таблиц, поэтому веб-аккаунту
    выдаём уникальный отрицательный идентификатор (не пересекается с
    настоящими Telegram id).
    """
    return -(int(secrets.token_hex(4), 16) % 900_000_000 + 100_000_000)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def auth_register(body: WebRegisterRequest, session: SessionDep) -> AuthResponse:
    """Регистрация на сайте: email + пароль → JWT + профиль."""
    settings = get_settings()
    email = _validate_email(body.email)
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )
    for _ in range(5):
        user = User(
            telegram_id=_synthetic_telegram_id(),
            email=email,
            password_hash=_hash_password(body.password),
            first_name=body.first_name,
            username=None,
        )
        session.add(user)
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()  # коллизия синтетического id — пробуем снова
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not allocate user id",
        )
    await session.refresh(user)
    return AuthResponse(
        access_token=create_access_token(user.telegram_id, settings),
        refresh_token=create_refresh_token(user.telegram_id, settings),
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=AuthResponse)
async def auth_login(body: WebLoginRequest, session: SessionDep) -> AuthResponse:
    """Вход на сайте: email + пароль → JWT + профиль."""
    settings = get_settings()
    email = _validate_email(body.email)
    found = await session.execute(select(User).where(User.email == email))
    user = found.scalar_one_or_none()
    if user is None or not user.password_hash or not _verify_password(
        body.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User is deactivated"
        )
    return AuthResponse(
        access_token=create_access_token(user.telegram_id, settings),
        refresh_token=create_refresh_token(user.telegram_id, settings),
        user=UserOut.model_validate(user),
    )
