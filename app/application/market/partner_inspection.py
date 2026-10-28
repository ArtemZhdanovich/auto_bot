"""Партнёрские чеки осмотра и история машины «как в Корее» (07 §3).

Владелец-партнёр управляет чеком и историей своих лотов (owner_partner_id
= счёту ключа X-API-Key; чужой/несуществующий лот неотличимы — 404).
Чек переиспользует admin-сценарии (`UpsertInspection`/`GetInspection`) —
проверка прав живёт здесь, а не в транспорте (вынос из listings.py:247).

Тарифный гейт чтения чека для владельца отключается (`bypass_tier=True`):
партнёр редактирует свой чек независимо от тарифа юзера; для всех
остальных GET /api/listings/{id}/inspection остаётся Pro+ (07 §3).
"""

from __future__ import annotations

import logging

from app.application.catalog.dto import InspectionUpdate, InspectionView
from app.application.errors import NotFound
from app.application.listings.use_cases import (
    GetInspection,
    UpsertInspection,
)
from app.application.market.ports import MarketLotRepository
from app.application.shared.ports import UnitOfWork

logger = logging.getLogger(__name__)

LOT_NOT_FOUND = "lot_not_found: лот не найден"

# История машины: сортировка при сохранении — свежие события первыми.
# Лимит <=50 событий и длины полей проверяет HTTP-схема (OwnerHistoryIn).


class GetPartnerInspection:
    """GET /api/partner/listings/{id}/inspection — чек своего лота."""

    def __init__(
        self, lots: MarketLotRepository, inspection: GetInspection
    ) -> None:
        self._lots = lots
        self._inspection = inspection

    async def execute(self, partner_id: int, listing_id: int) -> InspectionView:
        """Чек осмотра лота-владельца (без тарифного гейта).

        Raises:
            NotFound: Лот чужой/несуществует, неактивен или чека нет.
        """
        await _require_own_lot(self._lots, partner_id, listing_id)
        return await self._inspection.execute(
            listing_id, bypass_tier=True
        )


class UpsertPartnerInspection:
    """PUT /api/partner/listings/{id}/inspection — создать/заменить чек.

    Тело = admin `InspectionReportIn`; `source='partner'` независимо от
    поля тела (аудит происхождения чека). Неактивные лоты разрешены
    (семантика `UpsertInspection.exists`).
    """

    def __init__(
        self,
        lots: MarketLotRepository,
        upsert: UpsertInspection,
    ) -> None:
        self._lots = lots
        self._upsert = upsert

    async def execute(
        self, partner_id: int, listing_id: int, update: InspectionUpdate
    ) -> InspectionView:
        """Сохранить чек своего лота.

        Raises:
            NotFound: Лот чужой или не существует.
        """
        await _require_own_lot(self._lots, partner_id, listing_id)
        # source форсируется: чек создан партнёром, не admin/manual.
        view = await self._upsert.execute(
            listing_id,
            InspectionUpdate(
                source="partner",
                inspector=update.inspector,
                inspected_at=update.inspected_at,
                grade=update.grade,
                summary=update.summary,
                marks=update.marks,
                items=update.items,
                images=update.images,
            ),
        )
        logger.info(
            "partner_inspection_upserted partner_id=%s listing_id=%s grade=%s",
            partner_id,
            listing_id,
            view.grade,
        )
        return view


class UpdatePartnerLotHistory:
    """PUT /api/partner/listings/{id}/history — события истории машины.

    События сортируются по дате убыванию при сохранении: GET отдаёт
    `ListingOut.owner_history` как есть (PDP-таймлайн свежими первыми).
    """

    def __init__(self, lots: MarketLotRepository, uow: UnitOfWork) -> None:
        self._lots = lots
        self._uow = uow

    async def execute(
        self, partner_id: int, listing_id: int, events: list[dict]
    ) -> None:
        """Заменить историю своего лота.

        Raises:
            NotFound: Лот чужой или не существует.
        """
        await _require_own_lot(self._lots, partner_id, listing_id)
        ordered = sorted(
            events, key=lambda event: event["date"], reverse=True
        )
        updated = await self._lots.update_owner_history(
            listing_id, partner_id=partner_id, events=ordered
        )
        if not updated:
            raise NotFound(LOT_NOT_FOUND)
        await self._uow.commit()
        logger.info(
            "partner_history_updated partner_id=%s listing_id=%s events=%s",
            partner_id,
            listing_id,
            len(ordered),
        )


async def _require_own_lot(
    lots: MarketLotRepository, partner_id: int, listing_id: int
) -> None:
    """Лот существует и принадлежит счёту; иначе 404 (чужой = нет)."""
    lot = await lots.get_lot(listing_id)
    if (
        lot is None
        or lot.owner_type != "partner"
        or lot.owner_partner_id != partner_id
    ):
        raise NotFound(LOT_NOT_FOUND)
