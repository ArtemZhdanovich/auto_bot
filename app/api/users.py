"""Внутренние эндпоинты для бота (бот не ходит в БД напрямую — ТЗ п. 4.1)."""

from fastapi import APIRouter, Header

from app.core.deps_internal import check_internal_token
from app.core.deps import SessionDep
from app.schemas.user import UserOut, UserUpsert
from app.services.users import upsert_user

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/upsert", response_model=UserOut)
async def users_upsert(
    body: UserUpsert,
    session: SessionDep,
    x_internal_token: str | None = Header(default=None),
) -> UserOut:
    """Создать/обновить пользователя по данным из Telegram (для бота)."""
    check_internal_token(x_internal_token)
    user = await upsert_user(
        session,
        telegram_id=body.telegram_id,
        username=body.username,
        first_name=body.first_name,
        last_name=body.last_name,
        language_code=body.language_code,
    )
    return UserOut.model_validate(user)
