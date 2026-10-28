"""SQLAlchemy-реализация MarketLotRepository: owner-лоты рынка (§1-§3).

Вставка лота — `source='market'`, `source_id=uuid`, внутренний
`url=/listing/{id}` (id появляется после flush). Слоты партнёров и покупка
пакетов — условные `UPDATE ... WHERE <инвариант> RETURNING`: атомарно и
без отдельных SELECT-гонок; partner_tx пишется той же транзакцией.

Жизненный цикл (миграция 0030): статусные переходы (screening→publish,
patch, очередь модерации) — ORM-мутация строки под `FOR UPDATE`, а не
Core-UPDATE: Core экспирит объект в identity map и следующие `session.get`
падают на lazy-load вне greenlet (паттерн `update_owner_history`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Integer, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.market.dto import (
    LISTING_STATUS_ACCEPTED,
    LISTING_STATUS_ARCHIVED,
    MarketLotSnapshot,
    NewMarketLot,
)
from app.infrastructure.db.models import Listing, PartnerAccount, PartnerTransaction


def _snapshot(row: Listing) -> MarketLotSnapshot:
    """ORM-строка → снимок owner-лота."""
    return MarketLotSnapshot(
        listing_id=row.id,
        owner_type=row.owner_type,
        is_active=bool(row.is_active),
        owner_user_id=row.owner_user_id,
        owner_partner_id=row.owner_partner_id,
        owner_description=row.owner_description,
        placement_free_until=row.placement_free_until,
        placement_paid_until=row.placement_paid_until,
        make=row.make,
        model=row.model,
        year=row.year,
        price=row.price,
        currency=row.currency,
        mileage_km=row.mileage_km,
        fuel=row.fuel,
        transmission=row.transmission,
        images=tuple(row.images or ()),
        url=row.url,
        created_at=row.first_seen_at,
        # Гео (GEO-V1 §2): страна/город owner-лота из формы SellCar.
        country=row.country,
        city=row.city,
        status=row.status,
        rejected_reason=row.rejected_reason,
        moderation_payload=dict(row.moderation_payload)
        if row.moderation_payload is not None
        else None,
        pending_patch=dict(row.pending_patch) if row.pending_patch is not None else None,
    )


class SqlAlchemyMarketLotRepository:
    """Порт `MarketLotRepository` на `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_lot(self, lot: NewMarketLot) -> int:
        """Вставить owner-лот; url проставляется после flush по id."""
        command = lot.command
        assert command is not None  # сценарий всегда передаёт карточку
        source_id = uuid.uuid4().hex
        row = Listing(
            source="market",
            source_id=source_id,
            # Внутренний url: настоящий id появится после flush.
            url=f"/listing/market/{source_id}",
            make=command.make,
            model=command.model,
            year=command.year,
            price=command.price,
            currency=command.currency,
            mileage_km=command.mileage_km,
            fuel=command.fuel,
            transmission=command.transmission,
            images=list(command.photos),
            # Гео (GEO-V1 §2): страна продавца из формы + город (опционален).
            country=command.country,
            city=command.city,
            owner_type=lot.owner_type,
            owner_user_id=lot.owner_user_id,
            owner_partner_id=lot.owner_partner_id,
            owner_description=command.description,
            is_active=lot.is_active,
            placement_free_until=lot.placement_free_until,
            placement_paid_until=lot.placement_paid_until,
            status=lot.status,
            parsed_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        row.url = f"/listing/{row.id}"
        await self._session.flush()
        return row.id

    async def get_lot(self, listing_id: int) -> MarketLotSnapshot | None:
        """Снимок лота по id (в любом статусе) или None."""
        row = await self._session.get(Listing, listing_id)
        if row is None:
            return None
        return _snapshot(row)

    async def list_user_lots(self, user_id: int) -> list[MarketLotSnapshot]:
        """Все owner-лоты юзера (включая снятые), новые первыми."""
        rows = (
            await self._session.execute(
                select(Listing)
                .where(
                    Listing.owner_type == "user",
                    Listing.owner_user_id == user_id,
                )
                .order_by(Listing.id.desc())
            )
        ).scalars().all()
        return [_snapshot(row) for row in rows]

    async def list_partner_lots(self, partner_id: int) -> list[MarketLotSnapshot]:
        """Все owner-лоты партнёра (включая снятые), новые первыми."""
        rows = (
            await self._session.execute(
                select(Listing)
                .where(
                    Listing.owner_type == "partner",
                    Listing.owner_partner_id == partner_id,
                )
                .order_by(Listing.id.desc())
            )
        ).scalars().all()
        return [_snapshot(row) for row in rows]

    async def list_public_lots(
        self, *, country: str | None = None, limit: int = 60
    ) -> list[MarketLotSnapshot]:
        """Опубликованные owner-лоты рынка (витрина GET /api/market/lots).

        Только активные accepted (draft/moderation/rejected/expired не
        видны); `country` (GEO-V1 §2) — точный ISO-2, чипсы стран сверху.
        Новые первыми.
        """
        conditions = [
            Listing.owner_type.is_not(None),
            Listing.is_active.is_(True),
            Listing.status == LISTING_STATUS_ACCEPTED,
        ]
        if country:
            conditions.append(func.upper(Listing.country) == country.upper())
        rows = (
            await self._session.execute(
                select(Listing)
                .where(*conditions)
                .order_by(Listing.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [_snapshot(row) for row in rows]

    async def lock_user_lots(self, user_id: int) -> None:
        """Advisory-lock создания user-лотов: гонка двух параллельных
        POST /market/lots (оба видят 0 активных → оба получают free)
        сериализуется на ключе пользователя."""
        await self._session.execute(
            select(func.pg_advisory_xact_lock(9106, user_id % 2147483647))
        )

    async def count_active_user_lots(
        self, user_id: int, *, exclude_listing_id: int | None = None
    ) -> int:
        """Сколько активных owner-лотов юзера (правило «первый лот», §3)."""
        conditions = [
            Listing.owner_type == "user",
            Listing.owner_user_id == user_id,
            Listing.is_active.is_(True),
        ]
        if exclude_listing_id is not None:
            conditions.append(Listing.id != exclude_listing_id)
        return int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Listing).where(*conditions)
                )
            ).scalar_one()
        )

    async def has_recent_market_lots(
        self, user_id: int, *, days: int, exclude_listing_id: int | None = None
    ) -> bool:
        """Кулдаун бесплатного периода (аудит 06 §3): были ли owner-лоты
        юзера за последние `days` дней — по `first_seen_at`, включая
        снятые/истёкшие (ротация «снять/создать» не даёт новый free).
        """
        if days <= 0:
            return False
        conditions = [
            Listing.owner_type == "user",
            Listing.owner_user_id == user_id,
            Listing.first_seen_at >= datetime.now(UTC) - timedelta(days=days),
        ]
        if exclude_listing_id is not None:
            conditions.append(Listing.id != exclude_listing_id)
        return bool(
            (
                await self._session.execute(
                    select(literal(1)).where(*conditions).limit(1)
                )
            ).first()
        )

    async def count_paid_pending_lots(self, user_id: int) -> int:
        """Платные/ожидающие оплаты лоты юзера (квота §3).

        Платный = активный с placement_paid_until; ожидающий =
        одобренный (status='accepted') неактивный без placement-дат.
        Черновики/на модерации/отклонённые квоту не расходуют (10 §1).
        """
        return int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Listing)
                    .where(
                        Listing.owner_type == "user",
                        Listing.owner_user_id == user_id,
                        (
                            Listing.is_active.is_(True)
                            & Listing.placement_paid_until.is_not(None)
                        )
                        | (
                            Listing.is_active.is_(False)
                            & Listing.placement_free_until.is_(None)
                            & Listing.placement_paid_until.is_(None)
                            & (Listing.status == LISTING_STATUS_ACCEPTED)
                        ),
                    )
                )
            ).scalar_one()
        )

    async def has_accepted_user_lots(
        self, user_id: int, *, exclude_listing_id: int | None = None
    ) -> bool:
        """Есть ли у юзера одобренные owner-лоты кроме исключаемого (10 §2).

        Правило new_user: история продавца = хоть один accepted-лот
        (включая уже неактивные — проданные/снятые).
        """
        conditions = [
            Listing.owner_type == "user",
            Listing.owner_user_id == user_id,
            Listing.status == LISTING_STATUS_ACCEPTED,
        ]
        if exclude_listing_id is not None:
            conditions.append(Listing.id != exclude_listing_id)
        return bool(
            (
                await self._session.execute(
                    select(literal(1)).where(*conditions).limit(1)
                )
            ).first()
        )

    async def model_price_stats(
        self,
        make: str,
        model: str,
        year: int | None,
        currency: str | None,
        *,
        exclude_listing_id: int | None = None,
    ) -> tuple[int, Decimal | None]:
        """`(count, avg)` цен той же модели (год ±2, та же валюта, 10 §2).

        Упрощённые comps: внешние лоты + одобренные лоты рынка (status
        'accepted' — внешним backfill проставил его миграция 0030).
        """
        conditions = [
            func.lower(Listing.make) == make.lower(),
            func.lower(Listing.model) == model.lower(),
            Listing.price.is_not(None),
            Listing.status == LISTING_STATUS_ACCEPTED,
        ]
        if year is not None:
            conditions.append(Listing.year.between(year - 2, year + 2))
        if currency is not None:
            conditions.append(func.lower(Listing.currency) == currency.lower())
        if exclude_listing_id is not None:
            conditions.append(Listing.id != exclude_listing_id)
        row = (
            await self._session.execute(
                select(func.count(Listing.id), func.avg(Listing.price)).where(
                    *conditions
                )
            )
        ).one()
        count = int(row[0] or 0)
        avg = Decimal(str(row[1])) if row[1] is not None else None
        return count, avg

    async def has_duplicate_lot(
        self,
        owner_user_id: int,
        make: str,
        model: str,
        year: int | None,
        price: Decimal,
        *,
        exclude_listing_id: int | None = None,
        days: int = 7,
    ) -> bool:
        """Тот же owner+make+model+year+price за окно `days` дней (10 §2).

        Слабый ключ без VIN даёт ложные срабатывания — поэтому правило
        отправляет в ручную очередь, а не в reject (§7).
        """
        conditions = [
            Listing.owner_type == "user",
            Listing.owner_user_id == owner_user_id,
            func.lower(Listing.make) == make.lower(),
            func.lower(Listing.model) == model.lower(),
            Listing.year == year,
            Listing.price == price,
            Listing.first_seen_at
            >= datetime.now(UTC) - timedelta(days=days),
        ]
        if exclude_listing_id is not None:
            conditions.append(Listing.id != exclude_listing_id)
        return bool(
            (
                await self._session.execute(
                    select(literal(1)).where(*conditions).limit(1)
                )
            ).first()
        )

    async def _get_for_update(
        self,
        listing_id: int,
        *,
        owner_user_id: int | None = None,
    ) -> Listing | None:
        """Строка лота под FOR UPDATE; `owner_user_id` — проверка
        владения (None = любой владелец, админ-контур)."""
        conditions = [Listing.id == listing_id]
        if owner_user_id is not None:
            conditions += [
                Listing.owner_type == "user",
                Listing.owner_user_id == owner_user_id,
            ]
        return (
            await self._session.execute(
                select(Listing).where(*conditions).with_for_update()
            )
        ).scalar_one_or_none()

    async def mark_lot_screened(
        self,
        listing_id: int,
        *,
        status: str,
        owner_user_id: int | None = None,
        rejected_reason: str | None = None,
        moderation_payload: dict | None = None,
    ) -> bool:
        """Записать вердикт скрининга (reject/manual); False — не свой/нет."""
        row = await self._get_for_update(listing_id, owner_user_id=owner_user_id)
        if row is None:
            return False
        row.status = status
        row.rejected_reason = rejected_reason
        row.moderation_payload = moderation_payload
        await self._session.flush()
        await self._session.refresh(row)
        return True

    async def publish_lot_placement(
        self,
        listing_id: int,
        *,
        is_active: bool,
        placement_free_until: datetime | None,
        status: str = LISTING_STATUS_ACCEPTED,
        owner_user_id: int | None = None,
        moderation_payload: dict | None = None,
    ) -> bool:
        """Опубликовать лот: status='accepted', placement-решение сценария.

        Вызывается на accept скрининга (submit) и approve админа (10 §4):
        очистка rejected_reason, включение/ожидание оплаты по правилам §3.
        """
        row = await self._get_for_update(listing_id, owner_user_id=owner_user_id)
        if row is None:
            return False
        row.status = status
        row.rejected_reason = None
        if moderation_payload is not None:
            row.moderation_payload = moderation_payload
        row.is_active = is_active
        row.placement_free_until = placement_free_until
        await self._session.flush()
        await self._session.refresh(row)
        return True

    async def apply_lot_fields(
        self, listing_id: int, *, fields: dict, owner_user_id: int | None = None
    ) -> bool:
        """Применить словарь полей лота (правка черновика / accept патча)."""
        row = await self._get_for_update(listing_id, owner_user_id=owner_user_id)
        if row is None:
            return False
        column_by_key = {
            "make": "make",
            "model": "model",
            "year": "year",
            "price": "price",
            "currency": "currency",
            "mileage_km": "mileage_km",
            "fuel": "fuel",
            "transmission": "transmission",
            "description": "owner_description",
            # Гео (GEO-V1 §2): страна/город правятся патчем лота.
            "country": "country",
            "city": "city",
        }
        for key, value in fields.items():
            if key == "photos":
                row.images = list(value)
                continue
            column = column_by_key.get(key)
            if column is not None:
                setattr(row, column, value)
        await self._session.flush()
        await self._session.refresh(row)
        return True

    async def set_pending_patch(
        self,
        listing_id: int,
        patch: dict | None,
        *,
        owner_user_id: int | None = None,
    ) -> bool:
        """Записать/очистить версию-на-модерации (10 §1); False — не свой/нет."""
        row = await self._get_for_update(listing_id, owner_user_id=owner_user_id)
        if row is None:
            return False
        row.pending_patch = patch
        await self._session.flush()
        await self._session.refresh(row)
        return True

    async def list_moderation_queue(
        self, queue: str, *, limit: int = 50
    ) -> list[MarketLotSnapshot]:
        """Очередь ручной модерации (10 §2).

        'moderation' — лоты на проверке, риск (moderation_payload →
        risk_score) убыванием; 'patch' — правки опубликованных
        (pending_patch IS NOT NULL), новые первыми.
        """
        if queue == "moderation":
            # risk_score живёт в JSONB: → text → int, NULL — 0.
            risk_expr = func.coalesce(
                func.cast(
                    Listing.moderation_payload["risk_score"].astext, Integer
                ),
                0,
            )
            stmt = (
                select(Listing)
                .where(Listing.status == "moderation")
                .order_by(risk_expr.desc(), Listing.id.desc())
                .limit(limit)
            )
        else:
            stmt = (
                select(Listing)
                .where(Listing.pending_patch.is_not(None))
                .order_by(Listing.id.desc())
                .limit(limit)
            )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_snapshot(row) for row in rows]

    async def deactivate_lot(
        self,
        listing_id: int,
        *,
        user_id: int | None = None,
        partner_id: int | None = None,
    ) -> bool:
        """Снять свой лот с продажи: status='archived' + is_active=false
        (10 §1: is_active остаётся для старых фильтров совместимости).

        Закрыть можно лот в любом статусе кроме archived (черновик и
        ждущий оплаты тоже удаляются владельцем); повтор — False/404."""
        conditions = [
            Listing.id == listing_id,
            Listing.status != LISTING_STATUS_ARCHIVED,
        ]
        if user_id is not None:
            conditions += [
                Listing.owner_type == "user",
                Listing.owner_user_id == user_id,
            ]
        else:
            conditions += [
                Listing.owner_type == "partner",
                Listing.owner_partner_id == partner_id,
            ]
        result = await self._session.execute(
            update(Listing)
            .where(*conditions)
            .values(is_active=False, status=LISTING_STATUS_ARCHIVED)
        )
        return bool(result.rowcount)

    async def update_owner_history(
        self,
        listing_id: int,
        *,
        partner_id: int,
        events: list[dict],
    ) -> bool:
        """Заменить историю машины своего лота (07 §3); `False` — не свой/нет.

        ORM-мутация строки под `FOR UPDATE`, а не Core-UPDATE: Core-UPDATE
        экспирит объект в identity map сессии и следующие `session.get`
        падают на lazy-load вне greenlet. Сценарий уже отсортировал
        события по дате убыванием; commit — в сценарии.
        """
        row = (
            await self._session.execute(
                select(Listing)
                .where(
                    Listing.id == listing_id,
                    Listing.owner_type == "partner",
                    Listing.owner_partner_id == partner_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        row.owner_history = list(events)
        # `updated_at` — SQL-side onupdate: после flush атрибут помечен
        # expired и следующий getattr уедет в lazy-load вне greenlet.
        # refresh грузит серверные значения здесь, в async-контексте.
        await self._session.flush()
        await self._session.refresh(row)
        return True

    async def consume_placement_slot(self, partner_id: int) -> bool:
        """Атомарно занять слот (used+1); `False` — свободных слотов нет."""
        result = await self._session.execute(
            update(PartnerAccount)
            .where(
                PartnerAccount.id == partner_id,
                PartnerAccount.placement_slots
                - PartnerAccount.placement_slots_used
                > 0,
            )
            .values(
                placement_slots_used=PartnerAccount.placement_slots_used + 1
            )
        )
        return bool(result.rowcount)

    async def purchase_placement_package(
        self, partner_id: int, size: int, price: Decimal
    ) -> Decimal | None:
        """Атомарно купить пакет: balance-=price, slots+=size + partner_tx.

        `None` — не хватает баланса; списание и движение — одной
        транзакцией (commit — в сценарии).
        """
        result = await self._session.execute(
            update(PartnerAccount)
            .where(
                PartnerAccount.id == partner_id,
                PartnerAccount.balance >= price,
            )
            .values(
                balance=PartnerAccount.balance - price,
                placement_slots=PartnerAccount.placement_slots + size,
            )
            .returning(PartnerAccount.balance)
        )
        balance_after = result.scalar_one_or_none()
        if balance_after is None:
            return None
        self._session.add(
            PartnerTransaction(
                partner_id=partner_id,
                lead_id=None,
                amount=-price,
                kind="adjustment",
                balance_after=balance_after,
                comment=f"placement package {size}",
            )
        )
        return balance_after

    async def placement_slots(self, partner_id: int) -> tuple[int, int]:
        """`(slots, slots_used)` счёта партнёра."""
        row = (
            await self._session.execute(
                select(
                    PartnerAccount.placement_slots,
                    PartnerAccount.placement_slots_used,
                ).where(PartnerAccount.id == partner_id)
            )
        ).one_or_none()
        if row is None:
            return (0, 0)
        return int(row[0]), int(row[1])
