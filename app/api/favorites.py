"""Избранное (этап 6, ТЗ п. 4.3, 9.2.4: toggle на карточке, экран «Избранное»)."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentUserDep, SessionDep
from app.db.models import Favorite, Listing, ListingPriceHistory
from app.schemas.listing import ListingOut

router = APIRouter(prefix="/api", tags=["favorites"])


@router.get("/favorites", response_model=list[ListingOut])
async def list_favorites(user: CurrentUserDep, session: SessionDep) -> list[ListingOut]:
    """Все избранные объявления пользователя (новые сверху по времени добавления)."""
    result = await session.execute(
        select(Listing)
        .join(Favorite, Favorite.listing_id == Listing.id)
        .where(Favorite.user_id == user.telegram_id)
        .order_by(Favorite.created_at.desc())
    )
    listings = result.scalars().all()
    out = [ListingOut.model_validate(item) for item in listings]
    # Бейдж «цена снизилась»: первая точка истории по каждому объявлению —
    # одним батч-запросом (вместо N запросов на карточку)
    ids = [item.id for item in listings]
    if ids:
        history = await session.execute(
            select(ListingPriceHistory.listing_id, ListingPriceHistory.price)
            .where(ListingPriceHistory.listing_id.in_(ids))
            .order_by(
                ListingPriceHistory.listing_id,
                ListingPriceHistory.recorded_at,
                ListingPriceHistory.id,
            )
        )
        first_price: dict[int, float] = {}
        for row in history.all():
            first_price.setdefault(row.listing_id, row.price)
        for item in out:
            item.first_price = first_price.get(item.id)
    return out


@router.get("/favorites/{listing_id}")
async def is_favorite(
    listing_id: int, user: CurrentUserDep, session: SessionDep
) -> dict[str, bool]:
    result = await session.execute(
        select(Favorite).where(
            Favorite.user_id == user.telegram_id,
            Favorite.listing_id == listing_id,
        )
    )
    return {"favorited": result.scalar_one_or_none() is not None}


@router.post("/listings/{listing_id}/favorite")
async def toggle_favorite(
    listing_id: int, user: CurrentUserDep, session: SessionDep
) -> dict[str, bool]:
    """Toggle «В избранное» (ТЗ п. 9.2.4: «Избранное — toggle»)."""
    if await session.get(Listing, listing_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found"
        )
    result = await session.execute(
        select(Favorite).where(
            Favorite.user_id == user.telegram_id,
            Favorite.listing_id == listing_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.commit()
        return {"favorited": False}
    session.add(Favorite(user_id=user.telegram_id, listing_id=listing_id))
    await session.commit()
    return {"favorited": True}


@router.delete("/listings/{listing_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    listing_id: int, user: CurrentUserDep, session: SessionDep
) -> None:
    result = await session.execute(
        select(Favorite).where(
            Favorite.user_id == user.telegram_id,
            Favorite.listing_id == listing_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.commit()
