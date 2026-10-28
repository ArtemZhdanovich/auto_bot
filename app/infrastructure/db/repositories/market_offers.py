"""SQLAlchemy-реализация MarketRepository: витрина внутреннего рынка (§1).

Витрина = INNER JOIN partner_offers × listings (только активные лоты);
`add` ловит unique-нарушение listing_id → возвращает False (лот занят).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.market.dto import MarketOfferView
from app.infrastructure.db.models import Listing, PartnerOffer


def _offer_view(offer: PartnerOffer, listing: Listing) -> MarketOfferView:
    """JOIN-строка витрины → DTO-карточка с note и бейджем рынка."""
    images = listing.images or []
    return MarketOfferView(
        listing_id=listing.id,
        partner_id=offer.partner_id,
        note=offer.note,
        make=listing.make,
        model=listing.model,
        year=listing.year,
        price=float(listing.price) if listing.price is not None else None,
        currency=listing.currency,
        mileage_km=listing.mileage_km,
        url=listing.url,
        image=images[0] if images else None,
        has_market=True,
    )


class SqlAlchemyMarketRepository:
    """Порт `MarketRepository` на `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_offers(
        self,
        *,
        make: str | None = None,
        price_max: Decimal | None = None,
        limit: int = 20,
    ) -> list[MarketOfferView]:
        """Лента витрины: фильтры по марке (без регистра) и цене."""
        filters = [Listing.is_active.is_(True)]
        if make:
            filters.append(func.lower(Listing.make) == make.lower())
        if price_max is not None:
            filters.append(Listing.price <= price_max)
        stmt = (
            select(PartnerOffer, Listing)
            .join(Listing, Listing.id == PartnerOffer.listing_id)
            .where(*filters)
            .order_by(PartnerOffer.created_at.desc(), PartnerOffer.id.desc())
            .limit(limit)
        )
        return [
            _offer_view(offer, listing)
            for offer, listing in (await self._session.execute(stmt)).all()
        ]

    async def list_for_partner(self, partner_id: int) -> list[MarketOfferView]:
        """Витрина своего счёта (кабинет партнёра)."""
        stmt = (
            select(PartnerOffer, Listing)
            .join(Listing, Listing.id == PartnerOffer.listing_id)
            .where(PartnerOffer.partner_id == partner_id)
            .order_by(PartnerOffer.created_at.desc())
        )
        return [
            _offer_view(offer, listing)
            for offer, listing in (await self._session.execute(stmt)).all()
        ]

    async def partner_id_for_listing(self, listing_id: int) -> int | None:
        """Продавец лота (для seller-чата) или None → эскалация операторам."""
        return (
            await self._session.execute(
                select(PartnerOffer.partner_id).where(
                    PartnerOffer.listing_id == listing_id
                )
            )
        ).scalar_one_or_none()

    async def get_by_listing(
        self, listing_id: int
    ) -> tuple[int, str | None] | None:
        """`(partner_id, note)` лота витрины (для reuse в POST /offers)."""
        row = (
            await self._session.execute(
                select(PartnerOffer.partner_id, PartnerOffer.note).where(
                    PartnerOffer.listing_id == listing_id
                )
            )
        ).one_or_none()
        return (row.partner_id, row.note) if row is not None else None

    async def add(
        self, partner_id: int, listing_id: int, note: str | None
    ) -> bool:
        """Вставить оффер; `False` — listing_id уже занят (гонка/конфликт)."""
        try:
            # Savepoint: конфликт unique(listing_id) откатывает только вставку,
            # не транзакцию запроса.
            async with self._session.begin_nested():
                self._session.add(
                    PartnerOffer(
                        partner_id=partner_id, listing_id=listing_id, note=note
                    )
                )
                await self._session.flush()
        except IntegrityError:
            return False
        return True

    async def update_note(
        self, partner_id: int, listing_id: int, note: str | None
    ) -> bool:
        """Обновить заметку своего лота; `False` — не найден/чужой."""
        result = await self._session.execute(
            update(PartnerOffer)
            .where(
                PartnerOffer.partner_id == partner_id,
                PartnerOffer.listing_id == listing_id,
            )
            .values(note=note)
        )
        return bool(result.rowcount)

    async def remove(self, partner_id: int, listing_id: int) -> bool:
        """Снять свой лот с витрины; `False` — не найден/чужой."""
        result = await self._session.execute(
            delete(PartnerOffer).where(
                PartnerOffer.partner_id == partner_id,
                PartnerOffer.listing_id == listing_id,
            )
        )
        return bool(result.rowcount)
