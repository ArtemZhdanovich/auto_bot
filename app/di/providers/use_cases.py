"""Провайдеры сценариев (use cases) в REQUEST-скоупе.

Сценарии инжектят только порты (`Protocol`) и UoW — сборка автоматическая
(`provide_all`): контейнер сам свяжет инфраструктуру с интерфейсами по
подсказкам конструктора. Явные фабрики — только там, где нужен параметр
настроек (`GetListingRawPayload`).
"""

from __future__ import annotations

from dishka import Provider, Scope, provide, provide_all

from app.application.catalog.ports import Enqueuer
from app.application.chat.governance import (
    AdminKick,
    ArchiveThread,
    HandleReport,
    KickParticipant,
    LeaveThread,
    ListAdminReports,
    ListThreadEvents,
    ReportThread,
    ToggleMute,
)
from app.application.chat.link_preview import GetLinkPreview
from app.application.chat.bot import ReplySupportBot
from app.application.chat.summary import SummarizeThread
from app.application.chat.ports import (
    ChatPublisher,
    ChatRepository,
    ThreadEventPublisher,
    WsTokenFactory,
)
from app.application.chat.publisher import ParticipantEventPublisher
from app.application.chat.use_cases import (
    CountOperatorUnread,
    CreateGroupThread,
    CreateInvite,
    CreateSellerThread,
    CreateSupportThread,
    GetOnlineUsers,
    HandleSubscribeProxy,
    IssueWsToken,
    JoinByCode,
    ListMessages,
    ListMyThreads,
    ListOperatorMessages,
    ListOperatorThreads,
    ListPartnerThreads,
    MarkRead,
    SendMessage,
    SendOperatorMessage,
    SendTyping,
    SummonOperator,
)
from app.application.market.partner_inspection import (
    GetPartnerInspection,
    UpdatePartnerLotHistory,
    UpsertPartnerInspection,
)
from app.application.market.description import GenerateDescription
from app.application.market.moderation import ScreenMarketLot
from app.application.market.photos import UploadMarketPhoto
from app.application.market.use_cases import (
    AddMarketOffer,
    BuyPackage,
    CloseMarketLot,
    ClosePartnerLot,
    CreateMarketLot,
    CreatePartnerLot,
    ExtendLot,
    ListMarketLots,
    ListMarketOffers,
    ListModerationQueue,
    ListMyLots,
    ListPackages,
    ListPartnerLots,
    ListPartnerOffers,
    ModerateLot,
    ModeratePatch,
    RemoveMarketOffer,
    SubmitLot,
    UpdateLot,
)
from app.application.shared.ports import CachePort
from app.application.shared.ports import IdempotencyGuard, UnitOfWork

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
from app.application.analysis.history import ListMyAnalyses
from app.application.analysis.similar import FindSimilar
from app.application.analysis.use_cases import GetLatestAnalysis, RunAnalysis
from app.application.leads.deals import RequestAgent, RequestContact
from app.application.partners.market import GetPartnerMarket
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
from app.application.listings.history_report import BuildHistoryReport
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
    ListMyAnalyses,
    FindSimilar,
    RequestAgent,
    RequestContact,
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
    GetPartnerMarket,
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
    # Чаты (редизайн часть 5)
    ListMyThreads,
    ListPartnerThreads,
    CreateSupportThread,
    CreateSellerThread,
    ListMessages,
    MarkRead,
    HandleSubscribeProxy,
    GetLinkPreview,
    # RAG-бот поддержки (B2, §6)
    ReplySupportBot,
    # Операторский контур: зов оператора + админ-чаты (§2/§6)
    SummonOperator,
    ListOperatorThreads,
    CountOperatorUnread,
    ListOperatorMessages,
    SendOperatorMessage,
    # Личный канал (09 §1): typing-событие и онлайн-статусы
    SendTyping,
    GetOnlineUsers,
    # Переговорные чаты (07 §2)
    CreateGroupThread,
    CreateInvite,
    JoinByCode,
    # Говернанс переговорных (08 §2, §4-§6): kick/leave/archive, mute,
    # жалобы + админ-контур (события пишутся в тех же сценариях).
    KickParticipant,
    LeaveThread,
    ArchiveThread,
    ToggleMute,
    ReportThread,
    ListAdminReports,
    HandleReport,
    AdminKick,
    ListThreadEvents,
    # Внутренний рынок
    ListMarketOffers,
    ListPartnerOffers,
    AddMarketOffer,
    RemoveMarketOffer,
    # Лоты рынка с владельцем + монетизация размещений (редизайн части 6)
    CreateMarketLot,
    ListMyLots,
    # Витрина опубликованных лотов рынка с фильтром страны (GEO-V1 §2)
    ListMarketLots,
    CloseMarketLot,
    ExtendLot,
    ListPackages,
    BuyPackage,
    CreatePartnerLot,
    ListPartnerLots,
    ClosePartnerLot,
    # Жизненный цикл лота (редизайн часть 10): черновик/submit/правки,
    # авто-модерация и админ-очередь решений.
    ScreenMarketLot,
    SubmitLot,
    UpdateLot,
    ListModerationQueue,
    ModerateLot,
    ModeratePatch,
    # Фото лотов + партнёрские чеки/история (07 §1, §3)
    UploadMarketPhoto,
    GetPartnerInspection,
    UpsertPartnerInspection,
    UpdatePartnerLotHistory,
    # ИИ доверия (12 §1-§3): отчёт истории, генерация описания,
    # выжимки диалогов для оператора/партнёра.
    BuildHistoryReport,
    GenerateDescription,
    SummarizeThread,
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
    def thread_event_publisher(
        self, chats: ChatRepository, publisher: ChatPublisher
    ) -> ThreadEventPublisher:
        """Собрать единую точку публикации событий треда (09 §1).

        Обёртка над канальным паблишером: собирает участников треда и
        рассылает копию события в их личные каналы `user:{id}` /
        `partner:{id}` (публикаций в `thread:{id}` больше нет).

        Args:
            chats: Репозиторий чатов (участники и запись треда).
            publisher: Канальный паблишер centrifugo.

        Returns:
            Паблишер событий треда в личные каналы.
        """
        return ParticipantEventPublisher(chats, publisher)

    @provide
    def optional_thread_event_publisher(
        self, publisher: ThreadEventPublisher
    ) -> ThreadEventPublisher | None:
        """Тот же паблишер под union-ключом (опциональный параметр).

        `ClaimLead` принимает пушер как `ThreadEventPublisher | None` —
        пуш system-строки подключения агента не должен быть обязательным.

        Args:
            publisher: Паблишер событий треда.

        Returns:
            Тот же паблишер (`ThreadEventPublisher | None`).
        """
        return publisher

    @provide
    def send_message(
        self,
        chats: ChatRepository,
        publisher: ThreadEventPublisher,
        uow: UnitOfWork,
        settings: Settings,
    ) -> SendMessage:
        """Собрать отправку сообщений (маскировка контактов — 08 §3).

        `bot_username` — t.me-ссылки на нашего бота не маскируются;
        пустое значение = маскировать все t.me (безопаснее, 08 §9).

        Args:
            chats: Репозиторий чатов.
            publisher: Публикация в личные каналы участников (09 §1).
            uow: Границы транзакции.
            settings: Настройки (bot_username из BOT_USERNAME).

        Returns:
            Готовый `SendMessage`.
        """
        return SendMessage(
            chats, publisher, uow, bot_username=settings.bot_username
        )

    @provide
    def issue_ws_token(
        self, signer: WsTokenFactory, settings: Settings
    ) -> IssueWsToken:
        """Собрать выдачу ws-токенов (URL — из настроек, TTL — константа dto).

        Args:
            signer: Подпись HS256 (секрет CENTRIFUGO_SECRET).
            settings: Настройки приложения (centrifugo_url).

        Returns:
            Готовый `IssueWsToken`.
        """
        return IssueWsToken(
            signer,
            settings.centrifugo_public_url or settings.centrifugo_url,
        )

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

