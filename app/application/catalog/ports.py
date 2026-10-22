"""Порты каталога: хранилища объявлений, фильтров, очереди и внешние системы.

Реализации — в `infrastructure/` (SQLAlchemy, Redis, S3, httpx).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.application.catalog.dto import (
    FilterDraft,
    FilterPatch,
    FilterRecord,
    ListingCard,
    ListingFeedQuery,
    ListingPreview,
    InspectionUpdate,
    InspectionView,
    FeedCursor,
    PricePoint,
    NotifyTarget,
)
from app.application.dialog.dto import UserProfile
from app.application.shared.ports import LLMPort
from app.domain.search.criteria import SearchCriteria


class ListingReadRepository(Protocol):
    """Чтение объявлений для сценариев (реализация — SQLAlchemy)."""

    async def page(
        self, query: ListingFeedQuery, cursor: FeedCursor | None
    ) -> tuple[list[ListingCard], int | None, bool]:
        """Страница ленты, `total` (None — не считался) и флаг продолжения."""

    async def record_search(
        self, user_id: int | None, *, results: int, filters: int
    ) -> None:
        """Записать событие завершённого поиска (analytics)."""

    async def facets(self, make: str | None) -> dict[str, list[str]]:
        """Марки или модели выбранной марки для селектов."""

    async def find_by_url(self, needle: str) -> list[ListingCard]:
        """До двух активных объявлений по нормализованному хвосту URL."""

    async def get_active(self, listing_id: int) -> ListingCard | None:
        """Активное объявление или None."""

    async def enrich(self, listing_id: int) -> ListingCard | None:
        """Догрузить спеки из detail источника и вернуть карточку."""

    async def price_history(self, listing_id: int) -> list[PricePoint]:
        """История цен объявления (хронологически)."""

    async def exists(self, listing_id: int) -> bool:
        """Есть ли объявление с таким id (в любом статусе)."""

    async def get_report(self, listing_id: int) -> InspectionView | None:
        """Отчёт осмотра или None."""

    async def upsert_report(
        self, listing_id: int, update: InspectionUpdate
    ) -> InspectionView:
        """Создать/заменить отчёт осмотра."""


class ListingSearchRepository(Protocol):
    """Чтение объявлений по критериям (только превью)."""

    async def preview(
        self, criteria: SearchCriteria, limit: int = 3
    ) -> list[ListingPreview]:
        """До `limit` объявлений, свежие первыми."""


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


class Enqueuer(Protocol):
    """Порт постановки фонового задания."""

    async def enqueue(self, job: str) -> str:
        """Поставить задание и вернуть его `job_id`; ошибка — наружу."""


class NotifySettingsRepository(Protocol):
    """Хранилище настроек уведомлений (bulk-обновление фильтров)."""

    async def target(self, user_id: int) -> NotifyTarget | None:
        """Пользователь с полями тарифа или `None`, если его нет."""

    async def update_filters(
        self,
        user_id: int,
        *,
        notify_mode: str | None = None,
        quiet_hours: str | None = None,
        clear_quiet_hours: bool = False,
    ) -> int:
        """Применить настройки ко всем фильтрам пользователя, вернуть число строк."""


class UserLookupPort(Protocol):
    """Профиль пользователя для сценария анализа."""

    async def get(self, user_id: int) -> UserProfile | None:
        """Профиль или `None`."""


class LLMFactory(Protocol):
    """Фабрика LLM-клиента (`LLMPort`)."""

    def __call__(self) -> LLMPort:
        """Создать/получить клиент.

        Raises:
            AiUnavailable: Провайдер не сконфигурирован.
        """


class ObjectStoragePort(Protocol):
    """S3-совместимое хранилище."""

    async def put(
        self,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict | None = None,
    ) -> None: ...

    async def presign_get(self, key: str, ttl_sec: int) -> str: ...

    async def delete(self, key: str) -> None: ...


class TranslationPort(Protocol):
    """Батчевый перевод текстов; `None` в позиции — текст не переведён."""

    async def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str | None]: ...
