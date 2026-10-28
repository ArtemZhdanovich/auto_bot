"""Dishka-алиасы сценариев для роутеров (FastAPI `Depends` выпилен).

Раньше здесь был composition root на `Depends` (сессия → репозитории →
сценарии, ~900 строк). Теперь граф объектов собирает Dishka-контейнер
(`app/di`): сценарии инжектят порты, контейнер привязывает инфраструктуру.
Роутер получает готовый сценарий через `FromDishka[...]` и обязан
создаваться с `route_class=DishkaRoute`.
"""

from __future__ import annotations

from dishka.integrations.fastapi import FromDishka

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
from app.application.ai_search.use_cases import SendAiMessage
from app.application.analysis.history import ListMyAnalyses
from app.application.analysis.similar import FindSimilar
from app.application.analysis.use_cases import GetLatestAnalysis, RunAnalysis
from app.application.leads.deals import RequestAgent, RequestContact
from app.application.chat.bot import ReplySupportBot, SupportBotSpawner
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
from app.application.chat.summary import SummarizeThread
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
from app.application.partners.market import GetPartnerMarket
from app.application.dialog.use_cases import (
    FinalizeDialog,
    GetDialog,
    ResetDialog,
    RestoreDialog,
    SendDialogMessage,
    TriggerFetch,
)
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
from app.application.market.description import GenerateDescription
from app.application.market.use_cases import (
    AddMarketOffer,
    BuyPackage,
    CloseMarketLot,
    ClosePartnerLot,
    CreateMarketLot,
    CreatePartnerLot,
    ExtendLot,
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
from app.application.market.partner_inspection import (
    GetPartnerInspection,
    UpdatePartnerLotHistory,
    UpsertPartnerInspection,
)
from app.application.market.photos import UploadMarketPhoto
from app.application.metrics.use_cases import GetMetricsSnapshot
from app.application.notifications.settings import (
    TriggerScan,
    UpdateNotifySettings,
)
from app.application.partners.queries import (
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

# -- Избранное ---------------------------------------------------------------
ToggleFavoriteDep = FromDishka[ToggleFavorite]
ListFavoritesDep = FromDishka[ListFavorites]
IsFavoriteDep = FromDishka[IsFavorite]
ListFavoriteCardsDep = FromDishka[ListFavoriteCards]
RemoveFavoriteDep = FromDishka[RemoveFavorite]

# -- Уведомления и ручной скан ------------------------------------------------
UpdateNotifySettingsDep = FromDishka[UpdateNotifySettings]
TriggerScanDep = FromDishka[TriggerScan]

# -- Лиды, профиль, пользователь ----------------------------------------------
CreateLeadDep = FromDishka[CreateLead]
UpdateLeadDep = FromDishka[UpdateLead]
GetProfileDep = FromDishka[GetProfile]
CompleteOnboardingDep = FromDishka[CompleteOnboarding]
UpsertUserDep = FromDishka[UpsertUser]

# -- Диалог, AI-поиск и AI-анализ ----------------------------------------------
SendDialogMessageDep = FromDishka[SendDialogMessage]
ResetDialogDep = FromDishka[ResetDialog]
FinalizeDialogDep = FromDishka[FinalizeDialog]
RestoreDialogDep = FromDishka[RestoreDialog]
GetDialogDep = FromDishka[GetDialog]
DialogTriggerFetchDep = FromDishka[TriggerFetch]
SendAiMessageDep = FromDishka[SendAiMessage]
RunAnalysisDep = FromDishka[RunAnalysis]
GetLatestAnalysisDep = FromDishka[GetLatestAnalysis]
ListMyAnalysesDep = FromDishka[ListMyAnalyses]
FindSimilarDep = FromDishka[FindSimilar]
RequestAgentDep = FromDishka[RequestAgent]
RequestContactDep = FromDishka[RequestContact]

# -- Объявления ----------------------------------------------------------------
ListListingsDep = FromDishka[ListListings]
ListListingFacetsDep = FromDishka[ListListingFacets]
GetListingByUrlDep = FromDishka[GetListingByUrl]
GetListingDep = FromDishka[GetListing]
GetInspectionDep = FromDishka[GetInspection]
UpsertInspectionDep = FromDishka[UpsertInspection]
# AI-отчёт истории лота (12 §1)
BuildHistoryReportDep = FromDishka[BuildHistoryReport]

# -- Фильтры --------------------------------------------------------------------
ListFiltersDep = FromDishka[ListFilters]
CreateFilterDep = FromDishka[CreateFilter]
UpdateFilterDep = FromDishka[UpdateFilter]
DeleteFilterDep = FromDishka[DeleteFilter]

# -- Админ-панель и метрики мониторинга ------------------------------------------
ListAdapterClassesDep = FromDishka[ListAdapterClasses]
ListSourcesDep = FromDishka[ListSources]
UpsertSourceDep = FromDishka[UpsertSource]
GetSourceHealthDep = FromDishka[GetSourceHealth]
TriggerFetchDep = FromDishka[TriggerSourceFetch]
ListLeadsDep = FromDishka[ListLeads]
GetListingRawPayloadDep = FromDishka[GetListingRawPayload]
GetFunnelDep = FromDishka[GetFunnel]
GetRetentionDep = FromDishka[GetRetention]
AskAssistantDep = FromDishka[AskAssistant]
MetricsSnapshotDep = FromDishka[GetMetricsSnapshot]

# -- Партнёрский контур ------------------------------------------------------------
PartnerOverviewDep = FromDishka[GetPartnerOverview]
PartnerBillingDep = FromDishka[GetPartnerBilling]
PartnerLeadsDep = FromDishka[ListPartnerLeads]
ReadLeadsDep = FromDishka[ReadLeads]
ClaimLeadDep = FromDishka[ClaimLead]
ClaimBatchDep = FromDishka[ClaimBatch]
PartnerFeedbackDep = FromDishka[SetLeadFeedback]
PartnerTopUpDep = FromDishka[TopUpPartner]
TopUpUseCaseDep = FromDishka[TopUp]
ListPartnersDep = FromDishka[ListPartners]
CreatePartnerDep = FromDishka[CreatePartner]
RotatePartnerKeyDep = FromDishka[RotatePartnerKey]
RevokePartnerKeysDep = FromDishka[RevokePartnerKeys]
UpdatePartnerDep = FromDishka[UpdatePartner]
PartnerMarketDep = FromDishka[GetPartnerMarket]

# -- Платежи и подписка --------------------------------------------------------------
SubscriptionStatusDep = FromDishka[GetSubscriptionStatus]
UserSubscriptionDep = FromDishka[GetUserSubscription]
PendingPaymentDep = FromDishka[CreatePendingPayment]
ConfirmPaymentDep = FromDishka[ConfirmPayment]

# -- Чаты (редизайн часть 5) ---------------------------------------------------
ListMyThreadsDep = FromDishka[ListMyThreads]
CreateSupportThreadDep = FromDishka[CreateSupportThread]
CreateSellerThreadDep = FromDishka[CreateSellerThread]
ListMessagesDep = FromDishka[ListMessages]
SendMessageDep = FromDishka[SendMessage]
MarkReadDep = FromDishka[MarkRead]
IssueWsTokenDep = FromDishka[IssueWsToken]
# Личный канал (09 §1): typing другим участникам + онлайн-статусы
SendTypingDep = FromDishka[SendTyping]
GetOnlineUsersDep = FromDishka[GetOnlineUsers]
HandleSubscribeProxyDep = FromDishka[HandleSubscribeProxy]
GetLinkPreviewDep = FromDishka[GetLinkPreview]
ListPartnerThreadsDep = FromDishka[ListPartnerThreads]
ReplySupportBotDep = FromDishka[ReplySupportBot]
SupportBotSpawnerDep = FromDishka[SupportBotSpawner]
# Операторский контур поддержки (§2/§6: зов оператора + админ-чаты)
SummonOperatorDep = FromDishka[SummonOperator]
ListOperatorThreadsDep = FromDishka[ListOperatorThreads]
CountOperatorUnreadDep = FromDishka[CountOperatorUnread]
ListOperatorMessagesDep = FromDishka[ListOperatorMessages]
SendOperatorMessageDep = FromDishka[SendOperatorMessage]
# Переговорные чаты (07 §2)
CreateGroupThreadDep = FromDishka[CreateGroupThread]
CreateInviteDep = FromDishka[CreateInvite]
JoinByCodeDep = FromDishka[JoinByCode]
# Говернанс переговорных (08 §2, §4-§6)
KickParticipantDep = FromDishka[KickParticipant]
LeaveThreadDep = FromDishka[LeaveThread]
ArchiveThreadDep = FromDishka[ArchiveThread]
ToggleMuteDep = FromDishka[ToggleMute]
ReportThreadDep = FromDishka[ReportThread]
ListAdminReportsDep = FromDishka[ListAdminReports]
HandleReportDep = FromDishka[HandleReport]
AdminKickDep = FromDishka[AdminKick]
ListThreadEventsDep = FromDishka[ListThreadEvents]

# -- Внутренний рынок (витрина партнёров) ----------------------------------------
ListMarketOffersDep = FromDishka[ListMarketOffers]
ListPartnerOffersDep = FromDishka[ListPartnerOffers]
AddMarketOfferDep = FromDishka[AddMarketOffer]
RemoveMarketOfferDep = FromDishka[RemoveMarketOffer]

# -- Лоты рынка с владельцем + размещения (редизайн части 6 и 10) ---------------
CreateMarketLotDep = FromDishka[CreateMarketLot]
SubmitLotDep = FromDishka[SubmitLot]
UpdateLotDep = FromDishka[UpdateLot]
ListMyLotsDep = FromDishka[ListMyLots]
CloseMarketLotDep = FromDishka[CloseMarketLot]
ExtendLotDep = FromDishka[ExtendLot]
ListPackagesDep = FromDishka[ListPackages]
BuyPackageDep = FromDishka[BuyPackage]
CreatePartnerLotDep = FromDishka[CreatePartnerLot]
ListPartnerLotsDep = FromDishka[ListPartnerLots]
ClosePartnerLotDep = FromDishka[ClosePartnerLot]

# -- Админ-модерация объявлений (10 §2) -------------------------------------------
ListModerationQueueDep = FromDishka[ListModerationQueue]
ModerateLotDep = FromDishka[ModerateLot]
ModeratePatchDep = FromDishka[ModeratePatch]

# -- Фото лотов + партнёрские чеки/история (07 §1, §3) ---------------------------
UploadMarketPhotoDep = FromDishka[UploadMarketPhoto]
GetPartnerInspectionDep = FromDishka[GetPartnerInspection]
UpsertPartnerInspectionDep = FromDishka[UpsertPartnerInspection]
UpdatePartnerLotHistoryDep = FromDishka[UpdatePartnerLotHistory]

# -- ИИ доверия (12 §2-§3): генерация описания + выжимки диалогов ---------------
GenerateDescriptionDep = FromDishka[GenerateDescription]
SummarizeThreadDep = FromDishka[SummarizeThread]
