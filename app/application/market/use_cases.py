"""Сценарии витрины и лотов-с-владельцем внутреннего рынка.

Витрина (§1, partner_offers) остаётся кабинетом партнёра; рынок ленты —
лоты с владельцем (`owner_type`, §1-§3 редизайна части 6): правила
публикации (free-период / включённый лот платного тарифа / Stars),
продление размещений через контур payments (kind='placement') и пакеты
слотов партнёров.

Жизненный цикл (редизайн части 10): draft → submit → авто-модерация
(`moderation.py`) → accepted/rejected, версия-на-модерации при правке
опубликованного (pending_patch), админ-очередь и события `listing_status`
в личный канал centrifugo владельца.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application.billing.ports import PaymentRepository
from app.application.catalog.ports import ListingReadRepository, UserLookupPort
from app.application.errors import (
    Conflict,
    Forbidden,
    InvalidInput,
    NotFound,
    PaymentRequired,
)
from app.application.market.dto import (
    LISTING_STATUS_ACCEPTED,
    LISTING_STATUS_ARCHIVED,
    LISTING_STATUS_DRAFT,
    LISTING_STATUS_EXPIRED,
    LISTING_STATUS_MODERATION,
    LISTING_STATUS_REJECTED,
    PLACEMENT_EXPIRED,
    PLACEMENT_FREE,
    PLACEMENT_INCLUDED,
    PLACEMENT_NEEDS_PAYMENT,
    PLACEMENT_NONE,
    PLACEMENT_PAID,
    PLACEMENT_SLOT,
    CreatedLotView,
    CreateLotCommand,
    LotPatch,
    MarketLotSnapshot,
    MarketOfferView,
    ModerationOutcome,
    MyLotView,
    NewMarketLot,
    PackageView,
    PlacementInvoice,
    SubmittedLotView,
)
from app.application.market.moderation import (
    ACTION_MANUAL,
    ACTION_REJECT,
    ScreenMarketLot,
)
from app.application.market.ports import (
    MarketEventPublisher,
    MarketLotRepository,
    MarketRepository,
)
from app.application.market.quality import command_quality, lot_quality
from app.application.shared.ports import UnitOfWork
from app.config import Settings
from app.domain.pricing.ports import PriceAnalysisRepository

logger = logging.getLogger(__name__)


class ListMarketOffers:
    """GET /api/market/offers — лента витрины с фильтрами (§5)."""

    def __init__(self, market: MarketRepository) -> None:
        self._market = market

    async def execute(
        self,
        *,
        make: str | None = None,
        price_max: Decimal | None = None,
        limit: int = 20,
    ) -> list[MarketOfferView]:
        """Лоты витрины: карточка + partner note + бейдж рынка."""
        return await self._market.list_offers(
            make=make, price_max=price_max, limit=limit
        )


class ListPartnerOffers:
    """GET /api/partner/offers — витрина своего счёта (§5)."""

    def __init__(self, market: MarketRepository) -> None:
        self._market = market

    async def execute(self, partner_id: int) -> list[MarketOfferView]:
        """Лоты партнёра в витрине."""
        return await self._market.list_for_partner(partner_id)


class AddMarketOffer:
    """POST /api/partner/offers — выставить лот (reuse: заметка обновляется)."""

    def __init__(
        self,
        market: MarketRepository,
        listings: ListingReadRepository,
        uow: UnitOfWork,
    ) -> None:
        self._market = market
        self._listings = listings
        self._uow = uow

    async def execute(
        self, partner_id: int, listing_id: int, note: str | None
    ) -> bool:
        """Добавить лот или обновить свою заметку; `True` = создан.

        Raises:
            NotFound: Лота нет или он неактивен.
            Conflict: Лот уже в витрине у другого партнёра.
        """
        if await self._listings.get_active(listing_id) is None:
            raise NotFound("listing_not_found: лот недоступен")
        existing = await self._market.get_by_listing(listing_id)
        if existing is not None:
            owner, _ = existing
            if owner != partner_id:
                raise Conflict(
                    "offer_conflict: лот уже в витрине другого партнёра"
                )
            await self._market.update_note(partner_id, listing_id, note)
            await self._uow.commit()
            logger.info(
                "market_offer_updated partner_id=%s listing_id=%s",
                partner_id,
                listing_id,
            )
            return False
        if not await self._market.add(partner_id, listing_id, note):
            # Гонка двух партнёров на один лот: кто-то успел раньше.
            winner, _ = await self._market.get_by_listing(listing_id) or (
                None,
                None,
            )
            if winner != partner_id:
                raise Conflict(
                    "offer_conflict: лот уже в витрине другого партнёра"
                )
            return False
        logger.info(
            "market_offer_added partner_id=%s listing_id=%s",
            partner_id,
            listing_id,
        )
        await self._uow.commit()
        return True


class RemoveMarketOffer:
    """DELETE /api/partner/offers/{listing_id} — снять лот с витрины."""

    def __init__(self, market: MarketRepository, uow: UnitOfWork) -> None:
        self._market = market
        self._uow = uow

    async def execute(self, partner_id: int, listing_id: int) -> bool:
        """Снять свой лот; `False` → 404 (не найден или чужой)."""
        removed = await self._market.remove(partner_id, listing_id)
        if removed:
            await self._uow.commit()
            logger.info(
                "market_offer_removed partner_id=%s listing_id=%s",
                partner_id,
                listing_id,
            )
        return removed


# =========================================================================
# Лоты рынка с владельцем (§2-§3, миграция 0027; жизненный цикл — 10 §1)
# =========================================================================

# Тексты ошибок для клиента (транспорт отдаёт как detail HTTP-ошибок).
MARKET_GUEST_FORBIDDEN = (
    "market_registration_required: размещение доступно после входа "
    "или регистрации"
)
MARKET_LIMIT_REACHED = (
    "market_limit_reached: лимит объявлений исчерпан — Pro включает "
    "1 объявление, дополнительные публикуются за 20 Stars/неделя"
)
SLOTS_EXHAUSTED = (
    "placement_slots_exhausted: свободные размещения закончились — "
    "купите пакет размещений"
)
PACKAGE_NOT_FOUND = "package_not_found: такого пакета размещений нет"
INSUFFICIENT_BALANCE = (
    "insufficient_balance: не хватает баланса — пополните счёт партнёра"
)
INVALID_WEEKS = "invalid_weeks: доступно продление на 1 или 4 недели"
# Жизненный цикл (10 §1/§4): действие недоступно в текущем статусе.
INVALID_LOT_STATE = (
    "lot_invalid_state: действие недоступно в текущем статусе объявления"
)
NO_PENDING_PATCH = (
    "lot_patch_not_pending: у объявления нет правок на проверке"
)
LOT_NOT_FOUND = "lot_not_found: лот не найден"
USER_NOT_FOUND = "user_not_found: пользователь не найден"
ADMIN_REJECT_REASON = "lot_rejected_by_admin: отклонено модератором"
ADMIN_PATCH_REJECT_REASON = (
    "patch_rejected_by_admin: правки отклонены модератором"
)

# Допустимые окна продления (§2): 1 нед = 20★, 4 нед = 60★.
EXTEND_WEEKS = (1, 4)

# Очереди админ-модерации (10 §2): лоты на проверке и патчи опубликованных.
MODERATION_QUEUES = ("moderation", "patch")


def _coerce_patch_fields(fields: dict) -> dict:
    """Привести значения патча (JSONB) к типам колонок: str → Decimal/int.

    pending_patch хранится JSON-безопасно (Decimal → str); при применении
    к полям лота возвращаем в Decimal/Numeric и int-колонки.
    """
    coerced = dict(fields)
    if isinstance(coerced.get("price"), str):
        coerced["price"] = Decimal(coerced["price"])
    for key in ("year", "mileage_km"):
        if isinstance(coerced.get(key), str):
            coerced[key] = int(coerced[key])
    return coerced


def _aware(value: datetime) -> datetime:
    """Наивное время из БД трактуем как UTC (как в payments)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def placement_deadline(lot: MarketLotSnapshot) -> datetime | None:
    """Дедлайн размещения = max(free, paid); None — бессрочный (included/slot)."""
    moments = [
        _aware(value)
        for value in (lot.placement_free_until, lot.placement_paid_until)
        if value is not None
    ]
    return max(moments) if moments else None


def placement_status(
    lot: MarketLotSnapshot,
    *,
    now: datetime | None = None,
    effective_tier: str | None = None,
) -> str:
    """Placement-статус лота (§3): included/free/paid/expired/needs_payment/slot.

    «Expired» показывается только когда лот реально неживой: если владелец
    вернулся на платный тариф, живой лот без paid_until считается включённым
    (free_until от даунгрейда игнорируется — экспиратор его не трогает).

    Статусы жизненного цикла (10 §1) без публикации — draft/moderation/
    rejected — показываются как `none`: placement ещё не решался.
    """
    if lot.status in (
        LISTING_STATUS_DRAFT,
        LISTING_STATUS_MODERATION,
        LISTING_STATUS_REJECTED,
    ):
        return PLACEMENT_NONE
    if lot.owner_type == "partner":
        return PLACEMENT_SLOT
    if not lot.is_active and (
        lot.placement_free_until is None and lot.placement_paid_until is None
    ):
        # Создан как доп. лот и ждёт первой оплаты (или снят с продажи).
        return PLACEMENT_NEEDS_PAYMENT
    now = _aware(now or datetime.now(UTC))
    if (
        lot.placement_paid_until is not None
        and _aware(lot.placement_paid_until) > now
    ):
        return PLACEMENT_PAID
    if (
        lot.placement_free_until is not None
        and _aware(lot.placement_free_until) > now
    ):
        return PLACEMENT_FREE
    if lot.is_active and effective_tier in ("pro", "business"):
        # Живой лот платного тарифа: даунгрейд проставил free_until, но
        # после возврата тарифа экспиратор его не деактивирует.
        return PLACEMENT_INCLUDED
    if lot.placement_free_until is not None or lot.placement_paid_until is not None:
        return PLACEMENT_EXPIRED
    # Активный лот без дат: включённый лот платного тарифа (§3).
    return PLACEMENT_INCLUDED


def _lot_view(
    lot: MarketLotSnapshot,
    *,
    now: datetime | None,
    effective_tier: str | None = None,
    quality: dict | None = None,
) -> MyLotView:
    """Снимок лота → карточка «моих объявлений» (§2, 10 §4, 12 §2)."""
    payload = lot.moderation_payload or {}
    violations = tuple(
        dict(violation) for violation in (payload.get("violations") or [])
    )
    return MyLotView(
        listing_id=lot.listing_id,
        placement_status=placement_status(lot, now=now, effective_tier=effective_tier),
        is_active=lot.is_active,
        placement_free_until=lot.placement_free_until,
        placement_paid_until=lot.placement_paid_until,
        placement_deadline=placement_deadline(lot),
        make=lot.make,
        model=lot.model,
        year=lot.year,
        price=lot.price,
        currency=lot.currency,
        mileage_km=lot.mileage_km,
        images=lot.images,
        url=lot.url,
        owner_description=lot.owner_description,
        status=lot.status,
        rejected_reason=lot.rejected_reason,
        violations=violations,
        pending_patch=dict(lot.pending_patch) if lot.pending_patch else None,
        quality=quality,
    )


async def _publish_listing_status(
    publisher: MarketEventPublisher,
    lot: MarketLotSnapshot,
    *,
    status: str,
    reason: str | None = None,
    patch: str | None = None,
) -> None:
    """Событие `{type:'listing_status', listing_id, status, reason?}` в
    личный канал владельца — `user:{tg_id}` / `partner:{id}` (10 §3)."""
    if lot.owner_type == "user" and lot.owner_user_id is not None:
        channel = f"user:{lot.owner_user_id}"
    elif lot.owner_type == "partner" and lot.owner_partner_id is not None:
        channel = f"partner:{lot.owner_partner_id}"
    else:
        return
    event: dict = {
        "type": "listing_status",
        "listing_id": lot.listing_id,
        "status": status,
    }
    if reason:
        event["reason"] = reason
    if patch is not None:
        event["patch"] = patch
    await publisher.publish(channel, event)


class _PlacementDecision:
    """Решение placement-правил §3 (общее для submit-accept и админ-approve)."""

    __slots__ = ("is_active", "free_until", "placement_status", "needs_payment")

    def __init__(
        self,
        is_active: bool,
        free_until: datetime | None,
        placement: str,
        needs_payment: bool,
    ) -> None:
        self.is_active = is_active
        self.free_until = free_until
        self.placement_status = placement
        self.needs_payment = needs_payment


async def decide_placement(
    lots: MarketLotRepository,
    settings: Settings,
    *,
    user_id: int,
    paid_tier: bool,
    exclude_listing_id: int,
) -> _PlacementDecision:
    """Placement-правила первого/доп. лота (§3) в момент публикации.

    Тот же код, что раньше жил в `CreateMarketLot` (вынесен для reuse в
    submit и админ-approve, 10 §4): free-тариф без активных лотов и вне
    кулдауна → бесплатная неделя; платный тариф первый лот → включён;
    иначе `needs_payment` — публикация после оплаты Stars.
    """
    first_free = await lots.count_active_user_lots(
        user_id, exclude_listing_id=exclude_listing_id
    ) == 0 and (
        paid_tier
        or not await lots.has_recent_market_lots(
            user_id,
            days=settings.market_free_cooldown_days,
            exclude_listing_id=exclude_listing_id,
        )
    )
    if first_free:
        free_until = (
            None
            if paid_tier
            else datetime.now(UTC) + timedelta(days=settings.market_free_days)
        )
        return _PlacementDecision(
            is_active=True,
            free_until=free_until,
            placement=PLACEMENT_INCLUDED if paid_tier else PLACEMENT_FREE,
            needs_payment=False,
        )
    return _PlacementDecision(
        is_active=False,
        free_until=None,
        placement=PLACEMENT_NEEDS_PAYMENT,
        needs_payment=True,
    )


class CreateMarketLot:
    """POST /api/market/lots — создать лот юзера (10 §4).

    По умолчанию — черновик: placement-правила НЕ применяются (даты не
    ставятся), лот не виден в ленте; `submit=True` сразу прогоняет
    модерацию (флоу партнёров и старых клиентов не ломается).
    """

    def __init__(
        self,
        users: UserLookupPort,
        lots: MarketLotRepository,
        uow: UnitOfWork,
        settings: Settings,
        submit: SubmitLot,
        analyses: PriceAnalysisRepository | None = None,
    ) -> None:
        self._users = users
        self._lots = lots
        self._uow = uow
        self._settings = settings
        self._submit_lot = submit
        self._analyses = analyses

    async def execute(
        self, user_id: int, command: CreateLotCommand, *, submit: bool = False
    ) -> CreatedLotView:
        """Создать черновик (+submit → вердикт модерации).

        Raises:
            NotFound: Пользователя нет.
            Forbidden: Гостевой аккаунт (размещение после входа).
            Conflict: Исчерпана квота платных лотов (§3).
        """
        profile = await self._users.get(user_id)
        if profile is None:
            raise NotFound(USER_NOT_FOUND)
        if profile.is_guest:
            raise Forbidden(MARKET_GUEST_FORBIDDEN)
        # Сериализация параллельных созданий одного юзера (двойной клик):
        # оба запроса иначе видят «0 активных» и получают free-период.
        await self._lots.lock_user_lots(user_id)
        if (
            await self._lots.count_paid_pending_lots(user_id)
            >= self._settings.market_user_max_paid_lots
        ):
            raise Conflict(MARKET_LIMIT_REACHED)

        # Черновик (10 §1): placement не запускается — даты и is_active
        # решит submit после модерации.
        listing_id = await self._lots.create_lot(
            NewMarketLot(
                owner_type="user",
                owner_user_id=user_id,
                command=command,
                is_active=False,
                status=LISTING_STATUS_DRAFT,
            )
        )
        # Качество по полям формы создания (12 §2).
        quality = command_quality(command)
        if submit:
            submitted = await self._submit_lot.execute(user_id, listing_id)
            return CreatedLotView(
                listing_id=listing_id,
                placement_status=submitted.placement_status,
                is_active=submitted.is_active,
                needs_payment=submitted.needs_payment,
                status=submitted.status,
                quality=quality,
            )
        await self._uow.commit()
        logger.info(
            "market_lot_draft_created user_id=%s listing_id=%s",
            user_id,
            listing_id,
        )
        return CreatedLotView(
            listing_id=listing_id,
            placement_status=PLACEMENT_NONE,
            is_active=False,
            status=LISTING_STATUS_DRAFT,
            quality=quality,
        )


class SubmitLot:
    """POST /api/market/lots/{id}/submit — отправить на публикацию (10 §4).

    draft/rejected → синхронный авто-скрининг (`moderation.py`, правила
    быстрые — фоновой очереди не нужно): reject → rejected+reason;
    manual → moderation (ждёт админа); accept → placement-правила §3 и
    status='accepted' (needs_payment не блокирует модерацию: лот
    одобрен, но скрыт до оплаты).
    """

    def __init__(
        self,
        users: UserLookupPort,
        lots: MarketLotRepository,
        uow: UnitOfWork,
        settings: Settings,
        screen: ScreenMarketLot,
        publisher: MarketEventPublisher,
    ) -> None:
        self._users = users
        self._lots = lots
        self._uow = uow
        self._settings = settings
        self._screen = screen
        self._publisher = publisher

    async def execute(self, user_id: int, listing_id: int) -> SubmittedLotView:
        """Прогнать модерацию; результат = новый статус лота.

        Raises:
            NotFound: Лота нет/чужой или пользователя нет.
            Conflict: Статус не draft/rejected (10 §1) или квота §3.
        """
        profile = await self._users.get(user_id)
        if profile is None:
            raise NotFound(USER_NOT_FOUND)
        lot = await self._lots.get_lot(listing_id)
        if lot is None or lot.owner_type != "user" or lot.owner_user_id != user_id:
            raise NotFound(LOT_NOT_FOUND)
        if lot.status not in (LISTING_STATUS_DRAFT, LISTING_STATUS_REJECTED):
            raise Conflict(INVALID_LOT_STATE)
        await self._lots.lock_user_lots(user_id)
        if (
            await self._lots.count_paid_pending_lots(user_id)
            >= self._settings.market_user_max_paid_lots
        ):
            raise Conflict(MARKET_LIMIT_REACHED)

        verdict = await self._screen.execute(lot)
        payload = verdict.payload()
        violations = tuple(v.as_payload() for v in verdict.violations)

        if verdict.action == ACTION_REJECT:
            reason = verdict.rejection_reason()
            await self._lots.mark_lot_screened(
                listing_id,
                status=LISTING_STATUS_REJECTED,
                owner_user_id=user_id,
                rejected_reason=reason,
                moderation_payload=payload,
            )
            await self._uow.commit()
            await _publish_listing_status(
                self._publisher, lot, status=LISTING_STATUS_REJECTED, reason=reason
            )
            return SubmittedLotView(
                listing_id=listing_id,
                status=LISTING_STATUS_REJECTED,
                placement_status=PLACEMENT_NONE,
                is_active=False,
                rejected_reason=reason,
                violations=violations,
            )

        if verdict.action == ACTION_MANUAL:
            await self._lots.mark_lot_screened(
                listing_id,
                status=LISTING_STATUS_MODERATION,
                owner_user_id=user_id,
                moderation_payload=payload,
            )
            await self._uow.commit()
            await _publish_listing_status(
                self._publisher, lot, status=LISTING_STATUS_MODERATION
            )
            return SubmittedLotView(
                listing_id=listing_id,
                status=LISTING_STATUS_MODERATION,
                placement_status=PLACEMENT_NONE,
                is_active=False,
                violations=violations,
            )

        # accept: применяем placement-правила §3 и публикуем (10 §4) —
        # needs_payment НЕ блокирует модерацию: статус accepted, лот
        # скрыт до оплаты (placement_alive=false).
        decision = await decide_placement(
            self._lots,
            self._settings,
            user_id=user_id,
            paid_tier=profile.tier != "free",
            exclude_listing_id=listing_id,
        )
        await self._lots.publish_lot_placement(
            listing_id,
            owner_user_id=user_id,
            is_active=decision.is_active,
            placement_free_until=decision.free_until,
            moderation_payload=payload,
        )
        await self._uow.commit()
        await _publish_listing_status(
            self._publisher, lot, status=LISTING_STATUS_ACCEPTED
        )
        logger.info(
            "market_lot_submitted user_id=%s listing_id=%s placement=%s",
            user_id,
            listing_id,
            decision.placement_status,
        )
        return SubmittedLotView(
            listing_id=listing_id,
            status=LISTING_STATUS_ACCEPTED,
            placement_status=decision.placement_status,
            is_active=decision.is_active,
            needs_payment=decision.needs_payment,
            violations=violations,
        )


class UpdateLot:
    """PATCH /api/market/lots/{id} — правка лота с версионностью (10 §1, §4).

    draft/rejected обновляются напрямую; accepted складывает патч в
    `pending_patch` (лента продолжает показывать старые данные);
    moderation/archived/expired — 409.
    """

    def __init__(
        self,
        lots: MarketLotRepository,
        uow: UnitOfWork,
        analyses: PriceAnalysisRepository | None = None,
    ) -> None:
        self._lots = lots
        self._uow = uow
        self._analyses = analyses

    async def execute(
        self, user_id: int, listing_id: int, patch: LotPatch
    ) -> MyLotView:
        """Обновить лот/записать патч; вернуть свежую карточку my-lots.

        Raises:
            NotFound: Лота нет или он чужой.
            Conflict: Статус не позволяет правку (moderation/archived/expired).
        """
        lot = await self._lots.get_lot(listing_id)
        if lot is None or lot.owner_type != "user" or lot.owner_user_id != user_id:
            raise NotFound(LOT_NOT_FOUND)
        fields = patch.fields()
        if fields:
            if lot.status in (LISTING_STATUS_DRAFT, LISTING_STATUS_REJECTED):
                await self._lots.apply_lot_fields(
                    listing_id,
                    fields=_coerce_patch_fields(patch.json_fields()),
                    owner_user_id=user_id,
                )
            elif lot.status == LISTING_STATUS_ACCEPTED:
                # Версия-на-модерации: старые данные остаются видимыми;
                # патч хранится JSON-безопасно (Decimal → str).
                await self._lots.set_pending_patch(
                    listing_id, patch.json_fields(), owner_user_id=user_id
                )
            else:
                raise Conflict(INVALID_LOT_STATE)
            await self._uow.commit()
            logger.info(
                "market_lot_updated user_id=%s listing_id=%s status=%s patch=%s",
                user_id,
                listing_id,
                lot.status,
                lot.status == LISTING_STATUS_ACCEPTED,
            )
        fresh = await self._lots.get_lot(listing_id)
        assert fresh is not None  # только что читали/писали эту строку
        return _lot_view(
            fresh,
            now=datetime.now(UTC),
            quality=await lot_quality(fresh, self._analyses),
        )


class ListMyLots:
    """GET /api/market/my-lots — мои объявления с placement-статусом (§2)."""

    def __init__(
        self,
        lots: MarketLotRepository,
        users: "UserLookupPort | None" = None,
        analyses: PriceAnalysisRepository | None = None,
    ) -> None:
        self._lots = lots
        self._users = users
        self._analyses = analyses

    async def execute(self, user_id: int) -> list[MyLotView]:
        """Лоты юзера-владельца (включая снятые), новые первыми.

        effective_tier нужен только для косметики статуса: живой лот
        платного тарифа показывает «Включён», а не «Срок истёк».
        К каждому лоту прикладывается качество (12 §2).
        """
        from app.domain.subscription.policies import effective_tier

        now = datetime.now(UTC)
        tier: str | None = None
        if self._users is not None:
            profile = await self._users.get(user_id)
            if profile is not None:
                tier = effective_tier(profile)
        views: list[MyLotView] = []
        for lot in await self._lots.list_user_lots(user_id):
            views.append(
                _lot_view(
                    lot,
                    now=now,
                    effective_tier=tier,
                    quality=await lot_quality(lot, self._analyses),
                )
            )
        return views


class CloseMarketLot:
    """DELETE /api/market/lots/{id} — снять лот с продажи (§2, 10 §1)."""

    def __init__(
        self, lots: MarketLotRepository, uow: UnitOfWork, publisher: MarketEventPublisher
    ) -> None:
        self._lots = lots
        self._uow = uow
        self._publisher = publisher

    async def execute(self, user_id: int, listing_id: int) -> bool:
        """Снять свой лот (status='archived'); `False` → 404.

        is_active=false остаётся для совместимости со старыми фильтрами
        (10 §1); событие archived уходит в личный канал владельца.
        """
        closed = await self._lots.deactivate_lot(listing_id, user_id=user_id)
        if closed:
            await self._uow.commit()
            logger.info(
                "market_lot_closed user_id=%s listing_id=%s", user_id, listing_id
            )
            lot = await self._lots.get_lot(listing_id)
            if lot is not None:
                await _publish_listing_status(
                    self._publisher, lot, status=LISTING_STATUS_ARCHIVED
                )
        return closed


class ExtendLot:
    """POST /api/market/lots/{id}/extend — инвойс Stars на продление (§2).

    Создаёт pending-платёж kind='placement' (1 нед = 20★, 4 нед = 60★);
    вебхук (`ConfirmPayment`) продлевает `placement_paid_until`.
    Продлевать можно только опубликованные лоты (accepted/expired,
    10 §1): черновик и очередь модерации сначала проходят публикацию.
    """

    def __init__(
        self,
        lots: MarketLotRepository,
        payments: PaymentRepository,
        settings: Settings,
    ) -> None:
        self._lots = lots
        self._payments = payments
        self._settings = settings

    def price_for(self, weeks: int) -> int:
        """Цена окна в Stars: 1 нед = week_stars, 4 нед = week_stars*3 (§2)."""
        stars = self._settings.market_week_stars
        return stars * 3 if weeks >= 4 else stars

    async def execute(self, user_id: int, listing_id: int, weeks: int) -> PlacementInvoice:
        """Создать pending-платёж продления.

        Raises:
            InvalidInput: Недопустимое окно (только 1 или 4 недели).
            NotFound: Лота нет или он не принадлежит пользователю.
            Conflict: Лот не опубликован (draft/moderation/…, 10 §1).
        """
        if weeks not in EXTEND_WEEKS:
            raise InvalidInput(INVALID_WEEKS)
        lot = await self._lots.get_lot(listing_id)
        if lot is None or lot.owner_type != "user" or lot.owner_user_id != user_id:
            raise NotFound(LOT_NOT_FOUND)
        if lot.status not in (LISTING_STATUS_ACCEPTED, LISTING_STATUS_EXPIRED):
            raise Conflict(INVALID_LOT_STATE)
        amount = self.price_for(weeks)
        payment_id = await self._payments.create_pending_placement(
            user_id=user_id,
            amount=amount,
            listing_id=listing_id,
            weeks=weeks,
        )
        logger.info(
            "placement_invoice_created user_id=%s listing_id=%s weeks=%s",
            user_id,
            listing_id,
            weeks,
        )
        return PlacementInvoice(
            payment_id=payment_id,
            listing_id=listing_id,
            weeks=weeks,
            amount=amount,
        )


class ListPackages:
    """GET /api/partner/market/packages — пакеты размещений из конфига (§2)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self) -> list[PackageView]:
        """Пакеты `{size, price_rub}`, отсортированные по размеру."""
        return [
            PackageView(size=size, price_rub=price)
            for size, price in sorted(self._settings.market_packages.items())
        ]


class BuyPackage:
    """POST /api/partner/market/packages — купить пакет со баланса (§2).

    Атомарно в одной транзакции: `balance -= price`, `slots += size`,
    partner_tx kind='adjustment' с комментарием `placement package N`.
    В ответе — свободные места после покупки (`slots_free`).
    """

    def __init__(
        self,
        lots: MarketLotRepository,
        uow: UnitOfWork,
        settings: Settings,
    ) -> None:
        self._lots = lots
        self._uow = uow
        self._settings = settings

    async def execute(self, partner_id: int, size: int) -> PackageView:
        """Купить пакет; возвращает его размер, цену и остаток мест.

        Raises:
            NotFound: Пакета с таким размером нет в конфиге.
            PaymentRequired: Не хватает баланса (402).
        """
        price = self._settings.market_packages.get(size)
        if price is None:
            raise NotFound(PACKAGE_NOT_FOUND)
        if await self._lots.purchase_placement_package(partner_id, size, price) is None:
            raise PaymentRequired(INSUFFICIENT_BALANCE)
        await self._uow.commit()
        slots, used = await self._lots.placement_slots(partner_id)
        logger.info(
            "placement_package_bought partner_id=%s size=%s price=%s",
            partner_id,
            size,
            price,
        )
        return PackageView(
            size=size, price_rub=price, slots_free=max(slots - used, 0)
        )


class CreatePartnerLot:
    """POST /api/partner/market/lots — лот партнёра за слот (§2, 10 §4).

    Слот расходуется на публикацию (возврата нет); без свободных слотов —
    409 `placement_slots_exhausted`. Партнёрский submit автоматический:
    контент не экранится, но 10% выборки (сэмплинг, 10 §2) уходит в
    ручную модерацию — ответ всё равно 201, статус виден в поле `status`.
    """

    def __init__(
        self,
        lots: MarketLotRepository,
        uow: UnitOfWork,
        screen: ScreenMarketLot,
        publisher: MarketEventPublisher,
    ) -> None:
        self._lots = lots
        self._uow = uow
        self._screen = screen
        self._publisher = publisher

    async def execute(
        self, partner_id: int, command: CreateLotCommand
    ) -> CreatedLotView:
        """Разместить лот партнёра (без placement-дат — слот бессрочный).

        Raises:
            Conflict: Свободных слотов нет (409 placement_slots_exhausted).
        """
        if not await self._lots.consume_placement_slot(partner_id):
            raise Conflict(SLOTS_EXHAUSTED)
        listing_id = await self._lots.create_lot(
            NewMarketLot(
                owner_type="partner",
                owner_partner_id=partner_id,
                command=command,
                is_active=True,
                status=LISTING_STATUS_ACCEPTED,
            )
        )
        status = LISTING_STATUS_ACCEPTED
        lot = await self._lots.get_lot(listing_id)
        if lot is not None:
            verdict = await self._screen.execute(lot)
            if verdict.action == ACTION_MANUAL:
                await self._lots.mark_lot_screened(
                    listing_id,
                    status=LISTING_STATUS_MODERATION,
                    moderation_payload=verdict.payload(),
                )
                status = LISTING_STATUS_MODERATION
        await self._uow.commit()
        if lot is not None and status == LISTING_STATUS_MODERATION:
            await _publish_listing_status(
                self._publisher, lot, status=LISTING_STATUS_MODERATION
            )
        logger.info(
            "partner_lot_created partner_id=%s listing_id=%s status=%s",
            partner_id,
            listing_id,
            status,
        )
        return CreatedLotView(
            listing_id=listing_id,
            placement_status=PLACEMENT_SLOT,
            is_active=True,
            status=status,
            quality=command_quality(command),
        )


class ListPartnerLots:
    """GET /api/partner/market/lots — размещения партнёра (§2)."""

    def __init__(self, lots: MarketLotRepository) -> None:
        self._lots = lots

    async def execute(self, partner_id: int) -> list[MyLotView]:
        """Лоты партнёра-владельца (включая снятые), новые первыми."""
        now = datetime.now(UTC)
        return [
            _lot_view(lot, now=now)
            for lot in await self._lots.list_partner_lots(partner_id)
        ]


class ClosePartnerLot:
    """DELETE /api/partner/market/lots/{id} — снять размещение (§2)."""

    def __init__(
        self,
        lots: MarketLotRepository,
        uow: UnitOfWork,
        publisher: MarketEventPublisher,
    ) -> None:
        self._lots = lots
        self._uow = uow
        self._publisher = publisher

    async def execute(self, partner_id: int, listing_id: int) -> bool:
        """Снять свой лот (слот не возвращается); `False` → 404."""
        closed = await self._lots.deactivate_lot(listing_id, partner_id=partner_id)
        if closed:
            await self._uow.commit()
            logger.info(
                "partner_lot_closed partner_id=%s listing_id=%s",
                partner_id,
                listing_id,
            )
            lot = await self._lots.get_lot(listing_id)
            if lot is not None:
                await _publish_listing_status(
                    self._publisher, lot, status=LISTING_STATUS_ARCHIVED
                )
        return closed


# =========================================================================
# Админ-модерация (10 §2): ручная очередь лотов и патчей
# =========================================================================


class ListModerationQueue:
    """GET /api/admin/moderation — очередь ручной модерации (10 §2).

    `queue='moderation'` — лоты на проверке (риск убыванием);
    `queue='patch'` — правки опубликованных лотов (pending_patch).
    """

    def __init__(self, lots: MarketLotRepository) -> None:
        self._lots = lots

    async def execute(
        self, queue: str = "moderation", *, limit: int = 50
    ) -> list[MarketLotSnapshot]:
        """Снимки лотов очереди.

        Raises:
            InvalidInput: Неизвестная очередь (только moderation|patch).
        """
        if queue not in MODERATION_QUEUES:
            raise InvalidInput(
                "invalid_moderation_queue: очередь 'moderation' или 'patch'"
            )
        return await self._lots.list_moderation_queue(queue, limit=limit)


class ModerateLot:
    """POST /api/admin/moderation/{id}/approve|reject — решение по лоту (10 §2)."""

    def __init__(
        self,
        users: UserLookupPort,
        lots: MarketLotRepository,
        uow: UnitOfWork,
        settings: Settings,
        publisher: MarketEventPublisher,
    ) -> None:
        self._users = users
        self._lots = lots
        self._uow = uow
        self._settings = settings
        self._publisher = publisher

    async def execute(
        self, listing_id: int, *, approve: bool, reason: str | None = None
    ) -> ModerationOutcome:
        """Одобрить (placement-правила + accepted) или отклонить лот.

        Raises:
            NotFound: Лота нет.
            Conflict: Лот не в статусе moderation.
        """
        lot = await self._lots.get_lot(listing_id)
        if lot is None:
            raise NotFound(LOT_NOT_FOUND)
        if lot.status != LISTING_STATUS_MODERATION:
            raise Conflict(INVALID_LOT_STATE)

        if approve:
            if lot.owner_type == "partner":
                # Партнёрский лот из сэмплинга: слот уже оплачен.
                await self._lots.publish_lot_placement(
                    listing_id, is_active=True, placement_free_until=None
                )
                placement = PLACEMENT_SLOT
            else:
                owner_id = lot.owner_user_id or 0
                # Сериализуем с параллельным submit того же юзера.
                await self._lots.lock_user_lots(owner_id)
                profile = await self._users.get(owner_id)
                decision = await decide_placement(
                    self._lots,
                    self._settings,
                    user_id=owner_id,
                    paid_tier=bool(profile and profile.tier != "free"),
                    exclude_listing_id=listing_id,
                )
                await self._lots.publish_lot_placement(
                    listing_id,
                    owner_user_id=lot.owner_user_id,
                    is_active=decision.is_active,
                    placement_free_until=decision.free_until,
                )
                placement = decision.placement_status
            await self._uow.commit()
            await _publish_listing_status(
                self._publisher, lot, status=LISTING_STATUS_ACCEPTED
            )
            logger.info(
                "moderation_lot_approved listing_id=%s placement=%s",
                listing_id,
                placement,
            )
            return ModerationOutcome(
                listing_id=listing_id,
                status=LISTING_STATUS_ACCEPTED,
                placement_status=placement,
            )

        reason = (reason or "").strip() or ADMIN_REJECT_REASON
        await self._lots.mark_lot_screened(
            listing_id, status=LISTING_STATUS_REJECTED, rejected_reason=reason
        )
        await self._uow.commit()
        await _publish_listing_status(
            self._publisher, lot, status=LISTING_STATUS_REJECTED, reason=reason
        )
        logger.info("moderation_lot_rejected listing_id=%s", listing_id)
        return ModerationOutcome(
            listing_id=listing_id,
            status=LISTING_STATUS_REJECTED,
            reason=reason,
        )


class ModeratePatch:
    """POST /api/admin/moderation/{id}/patch_accept|patch_reject (10 §1-§2).

    accept — применить pending_patch к полям лота (photos целиком),
    очистить патч; reject — дописать `patch_reject_reason` внутрь патча
    (владелец видит в my-lots). Оба решения публикуют listing_status.
    """

    def __init__(
        self, lots: MarketLotRepository, uow: UnitOfWork, publisher: MarketEventPublisher
    ) -> None:
        self._lots = lots
        self._uow = uow
        self._publisher = publisher

    async def execute(
        self, listing_id: int, *, accept: bool, reason: str | None = None
    ) -> ModerationOutcome:
        """Решение по версии-на-модерации.

        Raises:
            NotFound: Лота нет.
            Conflict: У лота нет патча на проверке.
        """
        lot = await self._lots.get_lot(listing_id)
        if lot is None:
            raise NotFound(LOT_NOT_FOUND)
        if not lot.pending_patch:
            raise Conflict(NO_PENDING_PATCH)

        if accept:
            fields = _coerce_patch_fields(
                {
                    key: value
                    for key, value in lot.pending_patch.items()
                    if key != "patch_reject_reason"
                }
            )
            # Админ-контур: владелец не проверяется (owner_user_id=None).
            await self._lots.apply_lot_fields(listing_id, fields=fields)
            await self._lots.set_pending_patch(listing_id, None)
            await self._uow.commit()
            await _publish_listing_status(
                self._publisher,
                lot,
                status=lot.status,
                patch="accepted",
            )
            logger.info("moderation_patch_accepted listing_id=%s", listing_id)
            return ModerationOutcome(
                listing_id=listing_id, status=lot.status
            )

        reason = (reason or "").strip() or ADMIN_PATCH_REJECT_REASON
        patch = dict(lot.pending_patch)
        patch["patch_reject_reason"] = reason
        await self._lots.set_pending_patch(listing_id, patch)
        await self._uow.commit()
        await _publish_listing_status(
            self._publisher, lot, status=lot.status, reason=reason, patch="rejected"
        )
        logger.info("moderation_patch_rejected listing_id=%s", listing_id)
        return ModerationOutcome(
            listing_id=listing_id, status=lot.status, reason=reason
        )
