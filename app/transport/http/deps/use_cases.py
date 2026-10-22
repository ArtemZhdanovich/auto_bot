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
from app.application.analysis.use_cases import GetLatestAnalysis, RunAnalysis
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

# -- Объявления ----------------------------------------------------------------
ListListingsDep = FromDishka[ListListings]
ListListingFacetsDep = FromDishka[ListListingFacets]
GetListingByUrlDep = FromDishka[GetListingByUrl]
GetListingDep = FromDishka[GetListing]
GetInspectionDep = FromDishka[GetInspection]
UpsertInspectionDep = FromDishka[UpsertInspection]

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

# -- Платежи и подписка --------------------------------------------------------------
SubscriptionStatusDep = FromDishka[GetSubscriptionStatus]
UserSubscriptionDep = FromDishka[GetUserSubscription]
PendingPaymentDep = FromDishka[CreatePendingPayment]
ConfirmPaymentDep = FromDishka[ConfirmPayment]
