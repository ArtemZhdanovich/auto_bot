"""CRUD фильтров пользователя для Mini App (этап 5, ТЗ п. 9.2.3)."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentUserDep, SessionDep
from app.db.models import UserFilter
from app.schemas.filter import FilterCreate, FilterOut, FilterUpdate
from app.services.subscription import tier_config

router = APIRouter(prefix="/api/filters", tags=["filters"])


@router.get("", response_model=list[FilterOut])
async def list_filters(
    user: CurrentUserDep, session: SessionDep
) -> list[FilterOut]:
    result = await session.execute(
        select(UserFilter)
        .where(UserFilter.user_id == user.telegram_id)
        .order_by(UserFilter.created_at)
    )
    return [FilterOut.model_validate(f) for f in result.scalars().all()]


@router.post("", response_model=FilterOut, status_code=status.HTTP_201_CREATED)
async def create_filter(
    body: FilterCreate, user: CurrentUserDep, session: SessionDep
) -> FilterOut:
    """Создание фильтра: лимит по тарифу (3/20/∞) + instant только в Pro (ТЗ п. 10.2)."""
    cfg = tier_config(user)
    if body.notify_mode == "instant" and not cfg.instant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Instant notifications are a Pro feature — upgrade via /subscribe",
        )
    result = await session.execute(
        select(UserFilter).where(UserFilter.user_id == user.telegram_id)
    )
    count = len(list(result.scalars().all()))
    if cfg.max_filters is not None and count >= cfg.max_filters:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Filter limit reached ({cfg.max_filters} on {cfg.name} tier). "
                "Upgrade to Pro for 20 filters — /subscribe"
            ),
        )
    flt = UserFilter(
        user_id=user.telegram_id,
        name=body.name,
        criteria=body.criteria.model_dump(mode="json", exclude_none=True),
        notify_mode=body.notify_mode,
        quiet_hours=body.quiet_hours,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(flt)
    await session.commit()
    await session.refresh(flt)
    return FilterOut.model_validate(flt)


async def _get_own_filter(
    session: SessionDep, user_id: int, filter_id: int
) -> UserFilter:
    flt = await session.get(UserFilter, filter_id)
    if flt is None or flt.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found"
        )
    return flt


@router.patch("/{filter_id}", response_model=FilterOut)
async def update_filter(
    filter_id: int,
    body: FilterUpdate,
    user: CurrentUserDep,
    session: SessionDep,
) -> FilterOut:
    flt = await _get_own_filter(session, user.telegram_id, filter_id)
    if body.notify_mode == "instant" and not tier_config(user).instant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Instant notifications are a Pro feature — upgrade via /subscribe",
        )
    if body.name is not None:
        flt.name = body.name
    if body.criteria is not None:
        flt.criteria = body.criteria.model_dump(mode="json", exclude_none=True)
    if body.notify_mode is not None:
        flt.notify_mode = body.notify_mode
    if body.quiet_hours is not None:
        flt.quiet_hours = body.quiet_hours
    flt.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(flt)
    return FilterOut.model_validate(flt)


@router.delete("/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(
    filter_id: int, user: CurrentUserDep, session: SessionDep
) -> None:
    flt = await _get_own_filter(session, user.telegram_id, filter_id)
    await session.delete(flt)
    await session.commit()
