"""Профиль пользователя: GET /api/me (ТЗ п. 4.3)."""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.deps import CurrentUserDep, SessionDep
from app.schemas.user import UserOut

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUserDep) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/me/onboarded", response_model=UserOut)
async def me_onboarded(
    user: CurrentUserDep, session: SessionDep
) -> UserOut:
    """Пометить онбординг пройденным (UI/UX ТЗ §2.8). Идемпотентно."""
    if user.onboarded_at is None:
        user.onboarded_at = datetime.now(timezone.utc)
        await session.commit()
    return UserOut.model_validate(user)
