"""Сценарии CRUD фильтров пользователя.

Фильтры всегда принадлежат владельцу: репозиторий фильтрует по `user_id`,
чужой id превращается в 404 (`NotFound`), а не в 403 — не подтверждаем
существование чужого ресурса.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.dto.filters import FilterDraft, FilterPatch, FilterRecord
from app.application.errors import Conflict, Forbidden, NotFound
from app.application.shared.ports import UnitOfWork
from app.domain.subscription.policies import tier_config

logger = logging.getLogger(__name__)

FILTERS_LIST_MAX = 200
_INSTANT_FORBIDDEN = (
    "Instant notifications are a Pro feature — upgrade via /subscribe"
)


class FilterRepository(Protocol):
    """Хранилище фильтров пользователя."""

    async def list_for_user(
        self, user_id: int, *, limit: int, offset: int
    ) -> list[FilterRecord]:
        """Фильтры пользователя по `created_at`."""

    async def create(
        self,
        user_id: int,
        draft: FilterDraft,
        *,
        max_filters: int | None,
    ) -> FilterRecord | None:
        """Создать фильтр; `None` — исчерпан лимит тарифа."""

    async def get_owned(self, user_id: int, filter_id: int) -> FilterRecord | None:
        """Фильтр владельца или None (чужой неотличим от отсутствующего)."""

    async def update_owned(
        self,
        user_id: int,
        filter_id: int,
        patch: FilterPatch,
        *,
        updated_at: datetime,
    ) -> FilterRecord | None:
        """Применить патч к фильтру владельца."""

    async def delete_owned(self, user_id: int, filter_id: int) -> bool:
        """Удалить фильтр владельца; `False`, если не найден/чужой."""


@dataclass(frozen=True, slots=True)
class ListFiltersQuery:
    """Вход сценария списка фильтров."""

    user_id: int
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class CreateFilterCommand:
    """Вход сценария создания фильтра."""

    user: Any
    draft: FilterDraft


@dataclass(frozen=True, slots=True)
class UpdateFilterCommand:
    """Вход сценария обновления фильтра."""

    user: Any
    filter_id: int
    patch: FilterPatch


@dataclass(frozen=True, slots=True)
class DeleteFilterCommand:
    """Вход сценария удаления фильтра."""

    user: Any
    filter_id: int


def _require_active(user: Any) -> None:
    """403, если пользователь деактивирован.

    Raises:
        Forbidden: `is_active=False`.
    """
    if not getattr(user, "is_active", True):
        raise Forbidden("User is deactivated")


class ListFilters:
    """Список фильтров пользователя с пагинацией."""

    def __init__(self, filters: FilterRepository) -> None:
        self._filters = filters

    async def execute(self, query: ListFiltersQuery) -> list[FilterRecord]:
        """Вернуть страницу фильтров (1..200)."""
        limit = max(1, min(query.limit, FILTERS_LIST_MAX))
        return await self._filters.list_for_user(
            query.user_id, limit=limit, offset=max(0, query.offset)
        )


class CreateFilter:
    """Создание фильтра: лимит по тарифу (3/20/∞) + instant только в Pro."""

    def __init__(self, uow: UnitOfWork, filters: FilterRepository) -> None:
        self._uow = uow
        self._filters = filters

    async def execute(self, command: CreateFilterCommand) -> FilterRecord:
        """Создать фильтр.

        Raises:
            Forbidden: instant без Pro или пользователь деактивирован.
            Conflict: лимит фильтров тарифа исчерпан.
        """
        user = command.user
        _require_active(user)
        cfg = tier_config(user)
        if command.draft.notify_mode == "instant" and not cfg.instant:
            raise Forbidden(_INSTANT_FORBIDDEN)

        async with self._uow:
            record = await self._filters.create(
                user.telegram_id, command.draft, max_filters=cfg.max_filters
            )
            if record is None:
                raise Conflict(
                    f"Filter limit reached ({cfg.max_filters} on {cfg.name} tier). "
                    "Upgrade to Pro for 20 filters — /subscribe"
                )
            await self._uow.commit()

        logger.info(
            "filter_created user_id=%s filter_id=%s notify_mode=%s",
            user.telegram_id, record.id, record.notify_mode,
        )
        return record


class UpdateFilter:
    """Частичное обновление фильтра владельца."""

    def __init__(self, uow: UnitOfWork, filters: FilterRepository) -> None:
        self._uow = uow
        self._filters = filters

    async def execute(self, command: UpdateFilterCommand) -> FilterRecord:
        """Применить патч, проверив **итоговый** `notify_mode`.

        Пользователь, потерявший Pro, не сохранит `instant`, даже если в
        патче `notify_mode` не передан.

        Raises:
            Forbidden: instant без Pro или пользователь деактивирован.
            NotFound: Фильтра нет или он чужой.
        """
        user = command.user
        _require_active(user)
        current = await self._filters.get_owned(user.telegram_id, command.filter_id)
        if current is None:
            raise NotFound("Filter not found")

        final_notify_mode = (
            command.patch.notify_mode
            if command.patch.notify_mode is not None
            else current.notify_mode
        )
        if final_notify_mode == "instant" and not tier_config(user).instant:
            raise Forbidden(_INSTANT_FORBIDDEN)

        async with self._uow:
            record = await self._filters.update_owned(
                user.telegram_id,
                command.filter_id,
                command.patch,
                updated_at=datetime.now(UTC),
            )
            if record is None:
                raise NotFound("Filter not found")
            await self._uow.commit()

        logger.info(
            "filter_updated user_id=%s filter_id=%s notify_mode=%s",
            user.telegram_id, record.id, record.notify_mode,
        )
        return record


class DeleteFilter:
    """Удаление фильтра владельца."""

    def __init__(self, uow: UnitOfWork, filters: FilterRepository) -> None:
        self._uow = uow
        self._filters = filters

    async def execute(self, command: DeleteFilterCommand) -> None:
        """Удалить фильтр.

        Raises:
            Forbidden: Пользователь деактивирован.
            NotFound: Фильтра нет или он чужой.
        """
        user = command.user
        _require_active(user)
        async with self._uow:
            removed = await self._filters.delete_owned(
                user.telegram_id, command.filter_id
            )
            if not removed:
                raise NotFound("Filter not found")
            await self._uow.commit()
        logger.info(
            "filter_deleted user_id=%s filter_id=%s",
            user.telegram_id, command.filter_id,
        )
