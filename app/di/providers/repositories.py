"""Провайдеры репозиториев: порты → SQLAlchemy-реализации.

Ключ зависимости — `Protocol` из домена/прикладного слоя, значение —
адаптер из инфраструктуры. Сценарии инжектят интерфейс и не знают про
SQLAlchemy; тесты подменяют порт через контейнер без правок use case-ов.

Партнёрский `SqlAlchemyPartnerRepository` закрывает три порта сразу
(фасад/запросы/админка), рыночная аналитика партнёров —
`SqlAlchemyPartnerMarketRepository`, админский audit —
`SqlAlchemyAuditRepository`.
"""

from __future__ import annotations

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.ports import AdminStatsPort, SourceHealthPort
from app.application.admin.ports import (
    AdminSourcesPort,
    AuditPort,
)
from app.application.admin.ports import (
    AnalyticsRepository,
    FunnelRepository,
)
from app.application.audit.use_cases import LogAdminAction
from app.application.identity.ports import UserRepository as AuthUserRepository
from app.application.dialog.ports import (
    DialogRepositoryPort,
    EnabledSourcesPort,
)
from app.application.catalog.ports import FilterRepository
from app.application.chat.ports import ChatRepository, MarketLike
from app.application.market.ports import MarketLotRepository, MarketRepository
from app.application.leads.deals import ContactRateGate
from app.application.partners.ports import LeadRepository
from app.application.catalog.ports import ListingReadRepository
from app.application.admin.ports import MetricsPort
from app.application.catalog.ports import NotifySettingsRepository
from app.application.partners.ports import PartnerRepository
from app.application.partners.market import PartnerMarketRepository
from app.application.partners.ports import (
    PartnerAdminRepository,
    PartnerQueryRepository,
)
from app.application.billing.ports import PaymentRepository
from app.application.identity.ports import ProfileRepository
from app.application.shared.ports import AuditLog, UnitOfWork
from app.application.identity.ports import UserUpsertRepository as UsersRepository
from app.domain.audit.ports import AuditRepository
from app.domain.favorites.ports import FavoriteRepository
from app.domain.listings.ports import ListingRepository
from app.domain.pricing.ports import PriceAnalysisRepository
from app.infrastructure.db.outbox import OutboxDAO
from app.infrastructure.db.repositories.admin_sources import (
    SqlAlchemyAdminSourcesRepository,
)
from app.infrastructure.db.repositories.admin_stats import (
    SqlAlchemyAdminStatsRepository,
)
from app.infrastructure.db.repositories.analytics import (
    SqlAlchemyAnalyticsRepository,
)
from app.infrastructure.db.repositories.audit import SqlAlchemyAuditRepository
from app.infrastructure.db.repositories.dialog import SqlAlchemyDialogRepository
from app.infrastructure.db.repositories.chat import (
    SqlAlchemyChatRepository,
)
from app.infrastructure.db.repositories.market_offers import (
    SqlAlchemyMarketRepository,
)
from app.infrastructure.db.repositories.favorites import SqlAlchemyFavoriteRepository
from app.infrastructure.db.repositories.filters import SqlAlchemyFilterRepository
from app.infrastructure.db.repositories.funnels import SqlAlchemyFunnelRepository
from app.infrastructure.db.repositories.leads import (
    SqlAlchemyContactRateGate,
    SqlAlchemyLeadRepository,
)
from app.infrastructure.db.repositories.listings import SqlAlchemyListingRepository
from app.infrastructure.db.repositories.listings_read import (
    SqlAlchemyListingReadRepository,
)
from app.infrastructure.db.repositories.market import (
    SqlAlchemyPartnerMarketRepository,
)
from app.infrastructure.db.repositories.market_lots import (
    SqlAlchemyMarketLotRepository,
)
from app.infrastructure.db.repositories.metrics import SqlAlchemyMetricsRepository
from app.infrastructure.db.repositories.notifications import (
    SqlAlchemyNotificationSettingsRepository,
)
from app.infrastructure.db.repositories.parser_health import (
    SqlAlchemySourceHealthRepository,
)
from app.infrastructure.db.repositories.payments import SqlAlchemyPaymentRepository
from app.infrastructure.db.repositories.price_analysis import (
    SqlAlchemyPriceAnalysisRepository,
)
from app.infrastructure.db.repositories.profiles import SqlAlchemyProfileRepository
from app.infrastructure.db.repositories.users import SqlAlchemyUserRepository
from app.infrastructure.db.repositories.users_auth import (
    SqlAlchemyAuthUserRepository,
)
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.infrastructure.services.partners import SqlAlchemyPartnerRepository


class RepositoryProvider(Provider):
    """Порты → адаптеры в REQUEST-скоупе: живут на сессии запроса."""

    @provide
    def partner_market_repository(
        self, session: AsyncSession
    ) -> PartnerMarketRepository:
        """`PartnerMarketRepository` → `SqlAlchemyPartnerMarketRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий рынка для партнёрского контура.
        """
        return SqlAlchemyPartnerMarketRepository(session)

    scope = Scope.REQUEST

    @provide
    def unit_of_work(self, session: AsyncSession) -> UnitOfWork:
        """UoW без outbox (публикацию событий делает брокер/воркер).

        Args:
            session: Сессия текущего запроса.

        Returns:
            `SqlAlchemyUnitOfWork` под ключом порта `UnitOfWork`.
        """
        return SqlAlchemyUnitOfWork(
            session,
            OutboxDAO(session),
            publisher=None,
            use_outbox=False,
        )

    @provide
    def favorite_repository(self, session: AsyncSession) -> FavoriteRepository:
        """`FavoriteRepository` → `SqlAlchemyFavoriteRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий избранного.
        """
        return SqlAlchemyFavoriteRepository(session)

    @provide
    def listing_repository(self, session: AsyncSession) -> ListingRepository:
        """`ListingRepository` → `SqlAlchemyListingRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий объявлений.
        """
        return SqlAlchemyListingRepository(session)

    @provide
    def listing_read_repository(
        self, session: AsyncSession
    ) -> ListingReadRepository:
        """`ListingReadRepository` → `SqlAlchemyListingReadRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий чтения объявлений.
        """
        return SqlAlchemyListingReadRepository(session)

    @provide
    def optional_listing_read(
        self, repo: ListingReadRepository
    ) -> ListingReadRepository | None:
        """Тот же репозиторий под union-ключом (опциональные параметры).

        «Глубокий» разбор (Business) в `RunAnalysis` читает чек осмотра
        через необязательный порт: продаём его и под union-ключом, чтобы
        `provide_all` разрешал `ListingReadRepository | None`.

        Args:
            repo: Обязательный репозиторий чтения.

        Returns:
            Тот же репозиторий (`ListingReadRepository | None`).
        """
        return repo

    @provide
    def price_analysis_repository(
        self, session: AsyncSession
    ) -> PriceAnalysisRepository:
        """`PriceAnalysisRepository` → `SqlAlchemyPriceAnalysisRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий AI-разборов цены.
        """
        return SqlAlchemyPriceAnalysisRepository(session)

    @provide
    def optional_price_analysis_repository(
        self, repo: PriceAnalysisRepository
    ) -> PriceAnalysisRepository | None:
        """Тот же репозиторий под union-ключом (опциональный параметр).

        «Качество объявления» (12 §2) в my-lots/create/update берёт медиану
        рынка через `PriceAnalysisRepository | None` — продаём репозиторий и
        под union-ключом, чтобы `provide_all` разрешал необязательный порт.

        Args:
            repo: Обязательный репозиторий разборов.

        Returns:
            Тот же репозиторий (`PriceAnalysisRepository | None`).
        """
        return repo

    # -- фильтры, профили, пользователи, лиды, платежи, партнёры ------------

    @provide
    def filter_repository(self, session: AsyncSession) -> FilterRepository:
        """`FilterRepository` → `SqlAlchemyFilterRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий фильтров.
        """
        return SqlAlchemyFilterRepository(session)

    @provide
    def profile_repository(self, session: AsyncSession) -> ProfileRepository:
        """`ProfileRepository` → `SqlAlchemyProfileRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий профилей.
        """
        return SqlAlchemyProfileRepository(session)

    @provide
    def users_repository(self, session: AsyncSession) -> UsersRepository:
        """Порт `users` → `SqlAlchemyUserRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий пользователей (upsert из Telegram).
        """
        return SqlAlchemyUserRepository(session)

    @provide
    def auth_users_repository(self, session: AsyncSession) -> AuthUserRepository:
        """Порт `auth` → `SqlAlchemyAuthUserRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий пользователей для сценариев аутентификации.
        """
        return SqlAlchemyAuthUserRepository(session)

    @provide
    def lead_repository(self, session: AsyncSession) -> LeadRepository:
        """`LeadRepository` → `SqlAlchemyLeadRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий лидов.
        """
        return SqlAlchemyLeadRepository(session)

    @provide
    def contact_rate_gate(self, session: AsyncSession) -> ContactRateGate:
        """`ContactRateGate` → `SqlAlchemyContactRateGate`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Rate-гейт заявок «свяжитесь со мной» (sent_log kind='contact').
        """
        return SqlAlchemyContactRateGate(session)

    @provide
    def payment_repository(self, session: AsyncSession) -> PaymentRepository:
        """`PaymentRepository` → `SqlAlchemyPaymentRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий платежей и подписки.
        """
        return SqlAlchemyPaymentRepository(session)

    @provide
    def partner_repository(self, session: AsyncSession) -> PartnerRepository:
        """Порт фасада партнёров → `SqlAlchemyPartnerRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Партнёрский репозиторий (claim/пополнение).
        """
        return SqlAlchemyPartnerRepository(session)

    @provide
    def partner_query_repository(
        self, session: AsyncSession
    ) -> PartnerQueryRepository:
        """Порт чтения партнёров → `SqlAlchemyPartnerRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Партнёрский репозиторий (запросы и аутентификация ключа).
        """
        return SqlAlchemyPartnerRepository(session)

    @provide
    def partner_admin_repository(
        self, session: AsyncSession
    ) -> PartnerAdminRepository:
        """Порт админки партнёров → `SqlAlchemyPartnerRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Партнёрский репозиторий (создание/правки/список).
        """
        return SqlAlchemyPartnerRepository(session)

    @provide
    def chat_repository(self, session: AsyncSession) -> ChatRepository:
        """`ChatRepository` → `SqlAlchemyChatRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий тредов/сообщений/участников чатов.
        """
        return SqlAlchemyChatRepository(session)

    @provide
    def optional_chat_repository(
        self, chats: ChatRepository
    ) -> ChatRepository | None:
        """Тот же репозиторий под union-ключом (опциональный параметр).

        `RequestAgent`/`ClaimLead` принимают чаты как `ChatRepository | None`
        — контур чатов не должен быть обязательным для сценариев лидов.

        Args:
            chats: Обязательный репозиторий чатов.

        Returns:
            Тот же репозиторий (`ChatRepository | None`).
        """
        return chats

    @provide
    def market_repository(self, session: AsyncSession) -> MarketRepository:
        """`MarketRepository` → `SqlAlchemyMarketRepository` (витрина рынка).

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий витрины внутреннего рынка.
        """
        return SqlAlchemyMarketRepository(session)

    @provide
    def market_like(self, market: MarketRepository) -> MarketLike:
        """Тот же репозиторий под узким ключом (для CreateSellerThread).

        Args:
            market: Полный порт витрины.

        Returns:
            Репозиторий как `MarketLike` (подмножество методов).
        """
        return market  # type: ignore[return-value]

    @provide
    def market_lot_repository(self, session: AsyncSession) -> MarketLotRepository:
        """`MarketLotRepository` → `SqlAlchemyMarketLotRepository`.

        Owner-лоты рынка (user/partner) с placement-моделью и слотами
        партнёров (редизайн часть 6, §1-§3).

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий owner-лотов внутреннего рынка.
        """
        return SqlAlchemyMarketLotRepository(session)

    # -- аналитика, аудит, метрики, уведомления, админка, диалоги -----------

    @provide
    def analytics_repository(self, session: AsyncSession) -> AnalyticsRepository:
        """`AnalyticsRepository` → `SqlAlchemyAnalyticsRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий событий воронки.
        """
        return SqlAlchemyAnalyticsRepository(session)

    @provide
    def funnel_repository(self, session: AsyncSession) -> FunnelRepository:
        """`FunnelRepository` → `SqlAlchemyFunnelRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий воронки/удержания.
        """
        return SqlAlchemyFunnelRepository(session)

    @provide
    def audit_repository(self, session: AsyncSession) -> AuditRepository:
        """`AuditRepository` → `SqlAlchemyAuditRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий журнала admin-действий.
        """
        return SqlAlchemyAuditRepository(session)

    @provide
    def audit_port(self, session: AsyncSession) -> AuditPort:
        """`AuditPort` админ-источников → `SqlAlchemyAuditRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Журнал действий для `UpsertSource`.
        """
        return SqlAlchemyAuditRepository(session)

    @provide
    def audit_log(self, session: AsyncSession) -> AuditLog:
        """`AuditLog` партнёрского контура → `LogAdminAction.execute`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Колбэк записи admin-действия.
        """
        return LogAdminAction(SqlAlchemyAuditRepository(session)).execute

    @provide
    def optional_audit_log(self, audit: AuditLog) -> AuditLog | None:
        """Тот же audit-колбэк под union-ключом (параметр опционален).

        Args:
            audit: Обязательный колбэк журнала.

        Returns:
            Тот же колбэк (`AuditLog | None`).
        """
        return audit

    @provide
    def metrics_port(self, session: AsyncSession) -> MetricsPort:
        """`MetricsPort` → `SqlAlchemyMetricsRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий метрик мониторинга.
        """
        return SqlAlchemyMetricsRepository(session)

    @provide
    def notify_settings_repository(
        self, session: AsyncSession
    ) -> NotifySettingsRepository:
        """`NotifySettingsRepository` → `SqlAlchemyNotificationSettingsRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий настроек уведомлений.
        """
        return SqlAlchemyNotificationSettingsRepository(session)

    @provide
    def admin_sources(self, session: AsyncSession) -> AdminSourcesPort:
        """`AdminSourcesPort` → `SqlAlchemyAdminSourcesRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Админский read/write-репозиторий источников.
        """
        return SqlAlchemyAdminSourcesRepository(session)

    @provide
    def admin_stats(self, session: AsyncSession) -> AdminStatsPort:
        """`AdminStatsPort` → `SqlAlchemyAdminStatsRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий сводной статистики для AI-помощника.
        """
        return SqlAlchemyAdminStatsRepository(session)

    @provide
    def source_health(self, session: AsyncSession) -> SourceHealthPort:
        """`SourceHealthPort` → `SqlAlchemySourceHealthRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий здоровья источников.
        """
        return SqlAlchemySourceHealthRepository(session)

    @provide
    def dialog_repository(self, session: AsyncSession) -> DialogRepositoryPort:
        """`DialogRepositoryPort` → `SqlAlchemyDialogRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Репозиторий архива диалогов.
        """
        return SqlAlchemyDialogRepository(session)

    @provide
    def enabled_sources(self, session: AsyncSession) -> EnabledSourcesPort:
        """`EnabledSourcesPort` → `SqlAlchemyDialogRepository`.

        Args:
            session: Сессия текущего запроса.

        Returns:
            Чтение включённых источников (trigger fetch).
        """
        return SqlAlchemyDialogRepository(session)
