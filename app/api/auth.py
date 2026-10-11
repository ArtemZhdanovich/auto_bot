"""Авторизация: initData из Mini App, magic-link (ТЗ п. 4.3, 9.5)."""

from fastapi import APIRouter, HTTPException, status
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
