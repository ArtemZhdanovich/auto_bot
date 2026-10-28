"""Порты витрины и лотов-с-владельцем внутреннего рынка.

`MarketRepository` — витрина partner_offers (кабинет партнёра, §1).
`MarketLotRepository` — owner-лоты рынка с placement-моделью (§1-§3,
миграция 0027): правила публикаций в сценариях, SQL — в адаптере.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from app.application.market.dto import (
    MarketLotSnapshot,
    MarketOfferView,
    NewMarketLot,
)


class MarketEventPublisher(Protocol):
    """Публикация `listing_status` в личный канал centrifugo (10 §3).

    Канал владельца: `user:{telegram_id}` / `partner:{id}` — тот же
    адаптер, что у чатов (fire-and-forget: сбой пуша не ломает REST).
    """

    async def publish(self, channel: str, data: dict[str, Any]) -> None: ...


class MarketRepository(Protocol):
    """CRUD витрины + поиск продавца лота для seller-чата (§1, §5)."""

    async def list_offers(
        self,
        *,
        make: str | None = None,
        price_max: Decimal | None = None,
        limit: int = 20,
    ) -> list[MarketOfferView]:
        """Лоты витрины (INNER JOIN listings, только активные)."""

    async def list_for_partner(self, partner_id: int) -> list[MarketOfferView]:
        """Витрина своего счёта (для кабинета партнёра)."""

    async def partner_id_for_listing(self, listing_id: int) -> int | None:
        """Партнёр-продавец лота или None (чат эскалируется операторам)."""

    async def get_by_listing(
        self, listing_id: int
    ) -> tuple[int, str | None] | None:
        """`(partner_id, note)` лота витрины или None, если лота нет в ней."""

    async def add(self, partner_id: int, listing_id: int, note: str | None) -> bool:
        """Добавить лот в витрину; `False` — уже занят другим партнёром."""

    async def update_note(
        self, partner_id: int, listing_id: int, note: str | None
    ) -> bool:
        """Обновить заметку своего лота; `False` — не найден."""

    async def remove(self, partner_id: int, listing_id: int) -> bool:
        """Снять свой лот с витрины; `False` — не найден."""


class PhotosStoragePort(Protocol):
    """Публичное хранилище фото лотов рынка (07 §1).

    Реализация (`PhotosStorage`) сама создаёт bucket с policy public-read
    при первом использовании и собирает публичный URL из S3_PUBLIC_URL.
    """

    async def upload(self, user_id: int, content: bytes, ext: str) -> str:
        """Положить файл `photos/{user_id}/{uuid}.{ext}`; вернуть URL.

        Raises:
            Exception: Хранилище недоступно (сценарий вернёт 502/500).
        """


class MarketLotRepository(Protocol):
    """Owner-лоты рынка (user/partner) + слоты партнёров (§1-§3)."""

    async def create_lot(self, lot: NewMarketLot) -> int:
        """Вставить лот (source='market', source_id=uuid); вернуть id.

        `url` проставляется после вставки: внутренняя `/listing/{id}`.
        """

    async def get_lot(self, listing_id: int) -> MarketLotSnapshot | None:
        """Снимок лота по id (в любом статусе) или None."""

    async def list_user_lots(self, user_id: int) -> list[MarketLotSnapshot]:
        """Все лоты юзера-владельца (включая снятые), новые первыми."""

    async def list_partner_lots(
        self, partner_id: int
    ) -> list[MarketLotSnapshot]:
        """Все лоты партнёра-владельца (включая снятые), новые первыми."""

    async def count_active_user_lots(
        self, user_id: int, *, exclude_listing_id: int | None = None
    ) -> int:
        """Сколько активных owner-лотов юзера (правило «первый лот», §3).

        `exclude_listing_id` выкидывает сам отправляемый лот: черновик
        не должен занимать место первого (submit ≠ момент создания, §4).
        """

    async def count_paid_pending_lots(self, user_id: int) -> int:
        """Платные/ожидающие оплаты лоты юзера (квота max_user_paid_lots).

        Ожидающий = одобренный (status='accepted'), неактивный и без
        placement-дат: черновики и отклонённые квоту не расходуют (10 §1).
        """

    async def has_recent_market_lots(
        self, user_id: int, *, days: int, exclude_listing_id: int | None = None
    ) -> bool:
        """Были ли у юзера owner-лоты рынка за последние `days` дней.

        Кулдаун бесплатного периода (аудит 06 §3): считается по
        `first_seen_at` всех лотов юзера (включая снятые/истёкшие);
        `exclude_listing_id` исключает сам отправляемый черновик.
        """

    async def has_accepted_user_lots(
        self, user_id: int, *, exclude_listing_id: int | None = None
    ) -> bool:
        """Есть ли у юзера одобренные owner-лоты кроме указанного (10 §2).

        Правило `new_user`: владелец без единого accepted-лота (текущий
        не считается) попадает в ручную модерацию.
        """

    async def model_price_stats(
        self,
        make: str,
        model: str,
        year: int | None,
        currency: str | None,
        *,
        exclude_listing_id: int | None = None,
    ) -> tuple[int, Decimal | None]:
        """`(count, avg_price)` лотов той же модели (год ±2, та же валюта).

        Упрощённые comps для правила `price_outlier` (10 §2): внешние и
        одобренные лоты рынка; `avg=None` — сравнивать не с чем.
        """

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
        """Тот же owner+make+model+year+price за последние `days` дней (10 §2)."""

    async def mark_lot_screened(
        self,
        listing_id: int,
        *,
        status: str,
        owner_user_id: int | None = None,
        rejected_reason: str | None = None,
        moderation_payload: dict | None = None,
    ) -> bool:
        """Записать вердикт скрининга: status (+reason/payload); False — не свой/нет.

        Для reject/manual: `rejected_reason` только при отказе,
        `moderation_payload` — сработавшие правила и risk_score.
        """

    async def publish_lot_placement(
        self,
        listing_id: int,
        *,
        is_active: bool,
        placement_free_until: datetime | None,
        status: str = "accepted",
        owner_user_id: int | None = None,
        moderation_payload: dict | None = None,
    ) -> bool:
        """Применить placement-правила и опубликовать лот (accepted, 10 §4).

        status='accepted', reason отказа очищается; `is_active` и
        `placement_free_until` — решение сценария (free/included/
        needs_payment); `moderation_payload` сохраняет вердикт скрининга.
        """

    async def apply_lot_fields(
        self, listing_id: int, *, fields: dict, owner_user_id: int | None = None
    ) -> bool:
        """Применить словарь полей лота (правка черновика или accept патча).

        Ключи: make/model/year/price/currency/mileage_km/fuel/
        transmission/description/photos (список URL).
        """

    async def set_pending_patch(
        self, listing_id: int, patch: dict | None, *, owner_user_id: int | None = None
    ) -> bool:
        """Записать/очистить версию-на-модерации (10 §1); False — не свой/нет."""

    async def list_moderation_queue(
        self, queue: str, *, limit: int = 50
    ) -> list[MarketLotSnapshot]:
        """Очередь ручной модерации: `'moderation'` (риск убыванием) или
        `'patch'` (pending_patch IS NOT NULL, новые первыми)."""

    async def deactivate_lot(
        self,
        listing_id: int,
        *,
        user_id: int | None = None,
        partner_id: int | None = None,
    ) -> bool:
        """Снять свой лот с продажи (status='archived', is_active=false
        — для совместимости со старыми фильтрами, 10 §1); `False` — не
        свой/нет или уже снят."""

    async def update_owner_history(
        self,
        listing_id: int,
        *,
        partner_id: int,
        events: list[dict],
    ) -> bool:
        """Заменить историю машины своего лота (07 §3); `False` — не свой/нет."""

    async def consume_placement_slot(self, partner_id: int) -> bool:
        """Атомарно занять слот (used+1); `False` — свободных слотов нет."""

    async def purchase_placement_package(
        self, partner_id: int, size: int, price: Decimal
    ) -> Decimal | None:
        """Атомарно купить пакет: balance-=price, slots+=size + partner_tx.

        Возвращает новый баланс; `None` — не хватает баланса (или счёта
        нет). Списание, слоты и partner_tx — одной транзакцией (commit —
        в сценарии).
        """

    async def placement_slots(
        self, partner_id: int
    ) -> tuple[int, int]:
        """`(slots, slots_used)` счёта партнёра."""
