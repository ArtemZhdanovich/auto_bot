"""Провайдеры сценариев (use cases) в REQUEST-скоупе.

Сценарии инжектят только порты (`Protocol`) и UoW — сборка автоматическая
(`provide_all`): контейнер сам свяжет инфраструктуру с интерфейсами по
подсказкам конструктора. Явные фабрики — только там, где нужен параметр
настроек (`GetListingRawPayload`).
"""

from __future__ import annotations

from dishka import Provider, Scope, provide, provide_all

from app.application.catalog.ports import Enqueuer
from app.application.shared.ports import CachePort
from app.application.shared.ports import IdempotencyGuard

from app.application.admin.use_cases import (
    AskAssistant,
    GetFunnel,
    GetListingRawPayload,
    GetRetention,
    GetSourceHealth,
    ListAdapterClasses,
    ListLeads,
    ListSources,
    TriggerFetch as TriggerSourceFetch,
    UpsertSource,
)
from app.application.admin.ports import (
    AdminSourcesPort,
    RawPayloadStoragePort,
)
from app.application.ai_search.use_cases import (
    AiDailyQuota,
    MessageRateLimiter,
    SendAiMessage,
)
from app.application.analytics.use_cases import FunnelDashboard, RetentionDashboard
from app.application.auth.use_cases import (
    AuthenticateTelegram,
    CancelTelegramLogin,
    ConfirmTelegramLogin,
    CreateGuestSession,
    CreateTelegramLoginLink,
    GetCurrentUser,
    GetLoginContext,
    LoginWebUser,
    PollTelegramLogin,
    RefreshSession,
    RegisterWebUser,
)
from app.application.dialog.use_cases import (
    FinalizeDialog,
    GetDialog,
    ResetDialog,
    RestoreDialog,
    SendDialogMessage,
    TriggerFetch,
)
from app.application.analysis.use_cases import GetLatestAnalysis, RunAnalysis
from app.application.favorites.use_cases import (
    IsFavorite,
    ListFavoriteCards,
    ListFavorites,
    RemoveFavorite,
    ToggleFavorite,
)
from app.application.filters.use_cases import (
    CreateFilter,
    DeleteFilter,
    ListFilters,
    UpdateFilter,
)
from app.application.leads.use_cases import CreateLead, UpdateLead
from app.application.listings.use_cases import (
    GetInspection,
    GetListing,
    GetListingByUrl,
    ListListingFacets,
    ListListings,
    UpsertInspection,
)
from app.application.metrics.use_cases import GetMetricsSnapshot
from app.application.notifications.settings import (
    TriggerScan,
    UpdateNotifySettings,
)
from app.application.partners.queries import (
    AuthenticateApiKey,
    CreatePartner,
    GetPartnerBilling,
    GetPartnerOverview,
    ListPartnerLeads,
    ListPartners,
    ReadLeads,
    RevokePartnerKeys,
    RotatePartnerKey,
    SetLeadFeedback,
    TopUpPartner,
    UpdatePartner,
)
from app.application.partners.use_cases import ClaimBatch, ClaimLead, TopUp
from app.application.payments.use_cases import (
    ConfirmPayment,
    CreatePendingPayment,
    GetSubscriptionStatus,
    GetUserSubscription,
)
from app.application.profile.use_cases import CompleteOnboarding, GetProfile
from app.application.users.use_cases import UpsertUser
from app.config import Settings

_AUTO_WIRED = (
    # Избранное
    ToggleFavorite,
    ListFavorites,
    IsFavorite,
    ListFavoriteCards,
    RemoveFavorite,
    # Фильтры
    ListFilters,
    CreateFilter,
    UpdateFilter,
    DeleteFilter,
    # Лиды, профиль, пользователь
    CreateLead,
    UpdateLead,
    GetProfile,
    CompleteOnboarding,
    UpsertUser,
    # Объявления
    ListListings,
    ListListingFacets,
    GetListingByUrl,
    GetListing,
    GetInspection,
    UpsertInspection,
    # Метрики и уведомления
    GetMetricsSnapshot,
    UpdateNotifySettings,
    # Аналитика и админка
    FunnelDashboard,
    RetentionDashboard,
    GetFunnel,
    GetRetention,
    ListAdapterClasses,
    ListSources,
    UpsertSource,
    GetSourceHealth,
    TriggerSourceFetch,
    ListLeads,
    AskAssistant,
    # Диалог и AI-поиск
    TriggerFetch,
    SendDialogMessage,
    ResetDialog,
    FinalizeDialog,
    RestoreDialog,
    GetDialog,
    AiDailyQuota,
    SendAiMessage,
    # AI-анализ цены
    RunAnalysis,
    GetLatestAnalysis,
    # Партнёрский контур
    AuthenticateApiKey,
    GetPartnerOverview,
    GetPartnerBilling,
    ListPartnerLeads,
    ReadLeads,
    SetLeadFeedback,
    ClaimLead,
    ClaimBatch,
    TopUp,
    TopUpPartner,
    ListPartners,
    CreatePartner,
    RotatePartnerKey,
    RevokePartnerKeys,
    UpdatePartner,
    # Платежи и подписка
    GetSubscriptionStatus,
    GetUserSubscription,
    CreatePendingPayment,
    ConfirmPayment,
    # Аутентификация
    AuthenticateTelegram,
    CreateTelegramLoginLink,
    PollTelegramLogin,
    GetLoginContext,
    CancelTelegramLogin,
    ConfirmTelegramLogin,
    RefreshSession,
    RegisterWebUser,
    CreateGuestSession,
    LoginWebUser,
    GetCurrentUser,
)


class UseCaseProvider(Provider):
    """Сценарии: по одному объекту на запрос; порты инжектятся интерфейсами."""

    scope = Scope.REQUEST

    toggle_favorite = provide_all(*_AUTO_WIRED)

    @provide
    def trigger_scan(self, guard: IdempotencyGuard, enqueuer: Enqueuer) -> TriggerScan:
        """Собрать ручной скан (дедуп-ключ/TTL — дефолты сценария).

        Args:
            guard: Guard дедупликации (Redis `SET NX EX`).
            enqueuer: Поставщик задач в очередь.

        Returns:
            Готовый `TriggerScan`.
        """
        return TriggerScan(guard, enqueuer)

    @provide
    def message_rate_limiter(self, cache: CachePort) -> MessageRateLimiter:
        """Собрать per-user лимит сообщений (порог — константы сценария).

        Args:
            cache: Кэш Redis для счётчика.

        Returns:
            Готовый `MessageRateLimiter`.
        """
        return MessageRateLimiter(cache)

    @provide
    def admin_listing_raw(
        self,
        sources: AdminSourcesPort,
        storage: RawPayloadStoragePort,
        settings: Settings,
    ) -> GetListingRawPayload:
        """Собрать presign сырого payload (TTL — из настроек).

        Args:
            sources: Админский репозиторий источников.
            storage: Объектное хранилище.
            settings: Настройки приложения.

        Returns:
            Готовый `GetListingRawPayload`.
        """
        return GetListingRawPayload(
            sources,
            storage,
            settings.raw_presign_ttl_sec,
        )

