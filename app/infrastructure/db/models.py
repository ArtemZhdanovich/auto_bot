"""ORM-модели. Схема соответствует ТЗ п. 5.8 (основные таблицы).

Гарантии схемы:
- `server_default` на всех boolean/int/JSONB полях, где Python-side
  default недостаточно (raw SQL, сиды, миграции);
- индексы на `user_id`/`status`/`created_at` в таблицах, к которым
  есть частые `WHERE`/`ORDER BY`;
- `UNIQUE` на платёжных идемпотентных ключах (`provider_payment_id`,
  `Lead.idempotency_key`);
- `ON DELETE CASCADE` для user-facing данных (favorites, price_history,
  inspection_reports, price_analyses);
- денежные поля — `Numeric(12, 2)` (или `Numeric(20, 4)` где нужно).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Явные имена constraint'ов — для предсказуемых Alembic-миграций.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


# =========================================================================
# Users
# =========================================================================


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('buyer','dealer','salon','admin')",
            name="role_valid",
        ),
        CheckConstraint(
            "subscription_tier IN ('free','pro','business')",
            name="tier_valid",
        ),
        CheckConstraint(
            "email IS NULL OR email <> ''",
            name="email_not_empty",
        ),
        # Уникальность email case-insensitive — Postgres функциональный индекс.
        # (Уникальность по точному значению уже есть на колонке; этот индекс
        # защищает от дублей «User@ex.com» / «user@ex.com».)
        Index(
            "uq_users_email_lower",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index("idx_users_source_created", "source", "created_at"),
    )

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    language_code: Mapped[str | None] = mapped_column(String(8))
    role: Mapped[str] = mapped_column(
        String(16), default="buyer", server_default=text("'buyer'"), nullable=False
    )
    subscription_tier: Mapped[str] = mapped_column(
        String(16), default="free", server_default=text("'free'"), nullable=False
    )
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Источник создания: miniapp / bot / web / telegram_link. Для аналитики
    # воронки; NULL — старые записи (до миграции).
    source: Mapped[str | None] = mapped_column(String(16))


# =========================================================================
# Filters
# =========================================================================


class UserFilter(Base):
    __tablename__ = "filters"
    __table_args__ = (
        CheckConstraint(
            "notify_mode IN ('instant','digest','off')",
            name="notify_mode_valid",
        ),
        Index("idx_filters_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str | None] = mapped_column(String(64))
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    notify_mode: Mapped[str] = mapped_column(
        String(16), default="digest", server_default=text("'digest'"), nullable=False
    )
    notify_times: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    quiet_hours: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# =========================================================================
# Sources
# =========================================================================


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("kind IN ('api','parser','hybrid')", name="kind_valid"),
    )

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    adapter_class: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=100, server_default=text("100"), nullable=False
    )
    schedule_cron: Mapped[str | None] = mapped_column(String(64))
    rate_limit_per_min: Mapped[int] = mapped_column(
        Integer, default=60, server_default=text("60"), nullable=False
    )
    max_concurrency: Mapped[int] = mapped_column(
        Integer, default=3, server_default=text("3"), nullable=False
    )
    timeout_sec: Mapped[int] = mapped_column(
        Integer, default=30, server_default=text("30"), nullable=False
    )
    retry_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    credentials_ref: Mapped[str | None] = mapped_column(String(128))
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class SourceHealth(Base):
    __tablename__ = "source_health"

    source_name: Mapped[str] = mapped_column(
        ForeignKey("sources.name", ondelete="CASCADE"), primary_key=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_streak: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    avg_latency_ms: Mapped[int | None] = mapped_column(Integer)
    total_fetched: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )


# =========================================================================
# Notifications
# =========================================================================


class SentLog(Base):
    __tablename__ = "sent_log"
    __table_args__ = (
        Index("idx_sent_log_sent_at", "sent_at"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(
        String(32), primary_key=True, default="new", server_default=text("'new'")
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# =========================================================================
# Dialog sessions
# =========================================================================


class DialogSession(Base):
    __tablename__ = "dialog_sessions"
    __table_args__ = (
        Index("idx_dialog_sessions_user", "user_id"),
        Index("idx_dialog_sessions_updated", "updated_at"),
    )

    # TECHDEBT 3.6: default + server_default — раньше каждый insert обязан
    # был явно ставить id (иначе IntegrityError), при архивации из двух
    # мест это постоянно стреляло.
    id: Mapped[UUID] = mapped_column(
        PGUUID, primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    turn_count: Mapped[int | None] = mapped_column(Integer)
    messages: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# =========================================================================
# Payments
# =========================================================================


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "provider IS NULL OR provider IN ('stars','yookassa','crypto')",
            name="provider_valid",
        ),
        CheckConstraint(
            "tier IS NULL OR tier IN ('free','pro','business')",
            name="tier_valid",
        ),
        # Вид платежа (миграция 0027): подписка или продление размещения
        # лота рынка (kind='placement' — см. ConfirmPayment).
        CheckConstraint(
            "kind IN ('subscription','placement')",
            name="kind_valid",
        ),
        # Идемпотентность вебхуков: один provider_payment_id = один платёж.
        UniqueConstraint(
            "provider", "provider_payment_id",
            name="uq_payments_provider_payment_id",
        ),
        Index("idx_payments_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(16))
    provider_payment_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(16))
    tier: Mapped[str | None] = mapped_column(String(16))
    period_days: Mapped[int | None] = mapped_column(Integer)
    # Продление размещения: kind='placement' + предмет (lot юзера).
    kind: Mapped[str] = mapped_column(
        String(16), default="subscription",
        server_default=text("'subscription'"), nullable=False,
    )
    listing_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# =========================================================================
# Favorites
# =========================================================================


class Favorite(Base):
    """Избранное пользователя (ТЗ п. 4.3: POST /api/listings/{id}/favorite)."""

    __tablename__ = "favorites"
    __table_args__ = (PrimaryKeyConstraint("user_id", "listing_id"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False,
    )
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# =========================================================================
# Leads
# =========================================================================


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new','in_progress','closed','rejected')",
            name="status_valid",
        ),
        CheckConstraint(
            "partner_status IS NULL OR partner_status IN "
            "('claimed','accepted','returned','junk','deal')",
            name="partner_status_valid",
        ),
        # Идемпотентность создания (бот с ретраями): partial unique.
        Index(
            "uq_leads_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("idx_leads_user_created", "user_id", "created_at"),
        Index("idx_leads_status_created", "status", "created_at"),
        Index("idx_leads_partner_created", "partner_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False,
    )
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL")
    )
    source: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default="new", server_default=text("'new'"), nullable=False
    )
    manager_id: Mapped[int | None] = mapped_column(BigInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="SET NULL")
    )
    partner_status: Mapped[str | None] = mapped_column(String(16))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qualification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


# =========================================================================
# Listings
# =========================================================================


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_listings_source_source_id"),
        Index("idx_listings_make_model", "make", "model"),
        Index("idx_listings_year", "year"),
        Index("idx_listings_price", "price"),
        Index("idx_listings_vin", "vin"),
        Index("idx_listings_last_seen", "last_seen_at"),
        Index(
            "idx_listings_active",
            "is_active",
            postgresql_where=text("is_active = TRUE"),
        ),
        # Для ленты: (is_active, source, parsed_at DESC).
        Index(
            "idx_listings_active_source_parsed",
            "source",
            "parsed_at",
            postgresql_where=text("is_active = TRUE"),
        ),
        # Для фильтра по объёму двигателя.
        Index("idx_listings_engine_volume", "engine_volume"),
        CheckConstraint(
            "engine_volume IS NULL OR engine_volume BETWEEN 0 AND 20",
            name="engine_volume_range",
        ),
        # Рынок с владельцами (миграция 0027): owner_type ('user'|'partner')
        # и ровно один заполненный owner_id — два частичных CHECK.
        CheckConstraint(
            "owner_type IS NULL OR owner_type IN ('user','partner')",
            name="owner_type_valid",
        ),
        CheckConstraint(
            "owner_type IS DISTINCT FROM 'user' "
            "OR (owner_user_id IS NOT NULL AND owner_partner_id IS NULL)",
            name="owner_user_filled",
        ),
        CheckConstraint(
            "owner_type IS DISTINCT FROM 'partner' "
            "OR (owner_partner_id IS NOT NULL AND owner_user_id IS NULL)",
            name="owner_partner_filled",
        ),
        Index("idx_listings_owner", "owner_type", "owner_user_id", "owner_partner_id"),
        # Жизненный цикл owner-лота (миграция 0030, 10 §1): автомат
        # статусов модерации; очередь ручной модерации и патчей —
        # частичные индексы (строк в очереди мало).
        CheckConstraint(
            "status IN ('draft','moderation','accepted','rejected',"
            "'archived','expired')",
            name="listing_status_valid",
        ),
        Index("idx_listings_status", "status"),
        Index(
            "idx_listings_moderation_queue",
            "id",
            postgresql_where=text("status = 'moderation'"),
        ),
        Index(
            "idx_listings_pending_patch",
            "id",
            postgresql_where=text("pending_patch IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    make: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    year: Mapped[int | None] = mapped_column(Integer)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(String(8))
    mileage_km: Mapped[int | None] = mapped_column(Integer)
    vin: Mapped[str | None] = mapped_column(String(32))
    body_type: Mapped[str | None] = mapped_column(String(32))
    fuel: Mapped[str | None] = mapped_column(String(32))
    transmission: Mapped[str | None] = mapped_column(String(32))
    drive: Mapped[str | None] = mapped_column(String(16))
    engine_volume: Mapped[float | None] = mapped_column(Float)
    color: Mapped[str | None] = mapped_column(String(32))
    images: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    # Комплектация (миграция 0023): русифицированные опции из detail-карточки.
    options: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    raw_key: Mapped[str | None] = mapped_column(String(512))
    schema_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    content_hash: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    disappeared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    title_raw: Mapped[str | None] = mapped_column(Text)
    title_ru: Mapped[str | None] = mapped_column(Text)
    source_language: Mapped[str | None] = mapped_column(String(16))
    translated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # TECHDEBT 3.7: момент последней попытки обогащения спеками (detail
    # источника). NULL = ещё не пробовали; выставляется после ПЕРВОЙ
    # попытки — источники, не отдающие spec/rvo, не долбятся на каждом PDP.
    specs_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Владелец лота рынка (миграция 0027): user (частник) или partner
    # (салон). owner_type IS NULL — внешний лот (encar/kcar) без владельца.
    owner_type: Mapped[str | None] = mapped_column(String(8))
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.telegram_id"), nullable=True
    )
    owner_partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partner_accounts.id"), nullable=True
    )
    # Описание от владельца (PDP-блок «От владельца», ≤2000).
    owner_description: Mapped[str | None] = mapped_column(Text)
    # Размещение (user): конец бесплатного периода / оплачено до.
    placement_free_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    placement_paid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Жизненный цикл owner-лота (миграция 0030, 10 §1): статус автомата
    # модерации. Внешние лоты (парсеры) всегда 'accepted' — server_default
    # закрывает вставки без явного статуса.
    status: Mapped[str] = mapped_column(
        String(16), server_default=text("'accepted'"), nullable=False
    )
    # Причина отказа (правила авто-модерации или текст администратора).
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    # Последний скрининг: {violations: [{rule, detail, risk}], risk_score}.
    moderation_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Версия-на-модерации при правке опубликованного лота (10 §1):
    # NOT NULL = патч на проверке; отклонение дописывает
    # patch_reject_reason внутрь JSON.
    pending_patch: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # История машины «как в Корее» (миграция 0028): события партнёра
    # [{date, title, note?}] <=50, отдаётся в ListingOut для PDP-таймлайна.
    owner_history: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )


class ListingPriceHistory(Base):
    """Точка цены: строка только при изменении (PLAN.md, миграция 0009).

    PK — (id, recorded_at): ключ будущего RANGE (recorded_at) обязан
    входить в PK. `clock_timestamp()` (не `now()`) — чтобы в одной
    транзакции batch-вставки разные строки получали разные значения.
    """

    __tablename__ = "listing_price_history"
    __table_args__ = (
        Index("idx_price_history_listing", "listing_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(8))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        server_default=text("clock_timestamp()"),
        nullable=False,
    )


# =========================================================================
# Analytics
# =========================================================================


class AnalyticsEvent(Base):
    """Событие продуктовой воронки (P1-7)."""

    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("idx_analytics_name_created", "name", "created_at"),
        Index("idx_analytics_user_name", "user_id", "name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    props: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PriceAnalysis(Base):
    """AI-разбор цены объявления.

    Кэш общий на листинг: `latest_analysis` отдаёт последнюю запись
    по `listing_id ORDER BY created_at DESC LIMIT 1`. `user_id` — для
    аудита «кто запросил» и расчёта квоты (per-user-per-listing).
    """

    __tablename__ = "price_analyses"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('deal','fair','overpriced')",
            name="verdict_valid",
        ),
        Index("idx_price_analyses_listing_created", "listing_id", "created_at"),
        Index("idx_price_analyses_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    fair_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fair_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    summary: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# =========================================================================
# Partners
# =========================================================================


class PartnerAccount(Base):
    __tablename__ = "partner_accounts"
    __table_args__ = (
        CheckConstraint(
            "plan IN ('starter','pro','salon')", name="plan_valid",
        ),
        CheckConstraint(
            "status IN ('active','suspended')", name="status_valid",
        ),
        Index("idx_partner_accounts_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(128))
    plan: Mapped[str] = mapped_column(
        String(16), default="starter", server_default=text("'starter'"), nullable=False
    )
    daily_quota: Mapped[int] = mapped_column(
        Integer, default=10, server_default=text("10"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=text("'active'"), nullable=False
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"),
        server_default=text("0"),
    )
    cpl_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Владелец счёта (клиент/сотрудник партнёра): получает Business
    # автоматически, пока партнёр активен (прод-ревизия 2026-10).
    # Уникальный индекс — по naming-конвенции `uq_partner_accounts_...`.
    owner_telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    # Пакеты размещений на рынке (миграция 0027): bought = slots,
    # потрачено = slots_used; слот расходуется на публикацию (возврата нет).
    placement_slots: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    placement_slots_used: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerTransaction(Base):
    __tablename__ = "partner_transactions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('topup','charge_lead','adjustment')", name="kind_valid",
        ),
        Index("idx_partner_tx_partner_created", "partner_id", "created_at"),
        # TECHDEBT 2.4: двойное списание за один лид не поймать без
        # уникальности (lead_id, kind). Частичный индекс: NULL lead_id
        # (topup/adjustment) не ограничиваем.
        Index(
            "uq_partner_tx_lead_kind", "lead_id", "kind",
            unique=True, postgresql_where=text("lead_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="CASCADE"), nullable=False,
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerApiKey(Base):
    __tablename__ = "partner_api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_partner_api_keys_hash"),
        CheckConstraint(
            "status IN ('active','rotated','revoked')", name="status_valid",
        ),
        Index("idx_partner_api_keys_partner_status", "partner_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="CASCADE"), nullable=False,
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=text("'active'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# =========================================================================
# Audit
# =========================================================================


class AdminAction(Base):
    __tablename__ = "admin_actions"
    __table_args__ = (
        Index("idx_admin_actions_entity", "entity", "entity_id"),
        Index("idx_admin_actions_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(
        String(64), default="admin", server_default=text("'admin'"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# =========================================================================
# Inspection
# =========================================================================


class InspectionReport(Base):
    """Отчёт осмотра (миграция 0018): карта повреждений + чек-лист."""

    __tablename__ = "inspection_reports"
    __table_args__ = (
        Index("idx_inspection_reports_listing", "listing_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    source: Mapped[str] = mapped_column(
        String(16), default="manual", server_default=text("'manual'"), nullable=False
    )
    inspector: Mapped[str | None] = mapped_column(String(64))
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grade: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text)
    marks: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    items: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    images: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

# =========================================================================
# Chats (редизайн часть 5, миграция 0026)
# =========================================================================


class ChatThread(Base):
    """Тред чата: support (юзер ↔ бот/операторы), seller (юзер ↔ продавец
    лота), deal (юзер ↔ агент по лиду), group (переговорная до 5 человек,
    миграция 0028).

    `id` — UUID: имя centrifugo-канала `thread:{uuid}`. Уникальность «один
    тред на предмет» — частичными индексами по типу (см. `__table_args__`),
    поэтому повторное создание даёт reuse (POST .../seller-chat → 200).
    У group-тредов естественного ключа нет: сколько создано — столько есть.
    """

    __tablename__ = "chat_threads"
    __table_args__ = (
        CheckConstraint(
            "type IN ('support','seller','deal','group')", name="type_valid",
        ),
        CheckConstraint(
            "status IN ('active','closed')", name="status_valid",
        ),
        # Singleton поддержки: один тред на юзера.
        Index(
            "uq_chat_threads_support",
            "type", "user_id",
            unique=True, postgresql_where=text("type = 'support'"),
        ),
        # Один seller-тред на пару (юзер, лот).
        Index(
            "uq_chat_threads_seller",
            "type", "user_id", "listing_id",
            unique=True,
            postgresql_where=text("type = 'seller' AND listing_id IS NOT NULL"),
        ),
        # Один deal-тред на лид.
        Index(
            "uq_chat_threads_deal",
            "type", "lead_id",
            unique=True,
            postgresql_where=text("type = 'deal' AND lead_id IS NOT NULL"),
        ),
        Index("idx_chat_threads_user_last", "user_id", "last_message_at"),
        Index("idx_chat_threads_partner_last", "partner_id", "last_message_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID, primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Название переговорной (group, миграция 0028): задаёт юзер, 3-120
    # символов. Для support/seller/deal — NULL (заголовок генерится при
    # выдаче из типа и предмета).
    title: Mapped[str | None] = mapped_column(String(120))
    # Владелец-юзер (telegram_id); без FK — как dialog_sessions (исторические
    # треды не должны исчезать при каскадах users).
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    partner_id: Mapped[int | None] = mapped_column(BigInteger)
    listing_id: Mapped[int | None] = mapped_column(BigInteger)
    lead_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=text("'active'"),
        nullable=False,
    )
    bot_paused: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Момент закрытия (архив 08 §2 / пустой тред после leave): точка
    # отсчёта 90-дневного retention — закрытые старше скрываются фильтром
    # из списков чатов (08 §5, cron не нужен — данные остаются для аудита).
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    """Сообщение треда: markdown ≤4000 (лимит — Pydantic/сценарий).

    `sender_type` — user/operator/partner/bot/system; `sender_id` —
    telegram_id юзера или id партнёрского счёта (для bot/system — NULL).
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "sender_type IN ('user','operator','partner','bot','system')",
            name="sender_type_valid",
        ),
        # Пагинация вниз: WHERE thread_id=... ORDER BY id DESC LIMIT.
        Index("idx_chat_messages_thread_desc", "thread_id", text("id DESC")),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    thread_id: Mapped[UUID] = mapped_column(
        PGUUID,
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatParticipant(Base):
    """Участник треда + read-маркер (unread = сообщения после last_read).

    Владелец-юзер — неявный участник (строки может не быть до первого
    read); строка нужна партнёрам/операторам и для персистентных маркеров.
    """

    __tablename__ = "chat_participants"
    __table_args__ = (
        PrimaryKeyConstraint("thread_id", "user_type", "user_id"),
        CheckConstraint(
            "user_type IN ('user','operator','partner')", name="user_type_valid",
        ),
        # Говернанс (миграция 0029, 08 §2): владелец переговорной —
        # 'owner' (создатель), вошедшие по коду — 'member'.
        CheckConstraint(
            "role IN ('owner','member')", name="role_valid",
        ),
        Index("idx_chat_participants_user", "user_id"),
    )

    thread_id: Mapped[UUID] = mapped_column(
        PGUUID,
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_read_message_id: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(8), default="member", server_default=text("'member'"),
        nullable=False,
    )
    # Свои уведомления (08 §6): muted-тред не инкрементит бейдж конверта
    # (unread считается, фильтрует бейдж — фронт).
    muted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )


class ChatEvent(Base):
    """Аудит-событие треда (миграция 0029, 08 §5): операции говернанса.

    Пишется репозиторным методом `log_event` во всех операциях §2-§4;
    actor — инициатор (user/partner/operator; id — telegram_id или id
    счёта, для служебных субъектов NULL).
    """

    __tablename__ = "chat_events"
    __table_args__ = (
        CheckConstraint(
            "event IN ('created','joined','left','invited','kicked',"
            "'archived','reported','muted','unmuted')",
            name="event_valid",
        ),
        # Лента событий треда: новые сверху (ORDER BY id DESC).
        Index("idx_chat_events_thread_id", "thread_id", "id"),
        # Поток по типу события (например, все kick/reported за период).
        Index("idx_chat_events_event_created", "event", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    thread_id: Mapped[UUID] = mapped_column(
        PGUUID,
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_type: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatReport(Base):
    """Жалоба участника на тред (миграция 0029, 08 §4).

    Идемпотентность «открытой» жалобы — частичный unique (thread,
    reporter, reason) WHERE status='new': повтор → 200 без новой строки;
    после обработки (resolved/dismissed) пожаловаться можно снова.
    """

    __tablename__ = "chat_reports"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('spam','fraud','offtop','abuse')", name="reason_valid",
        ),
        CheckConstraint(
            "status IN ('new','resolved','dismissed')", name="status_valid",
        ),
        Index("idx_chat_reports_status_created", "status", "created_at"),
        Index(
            "uq_chat_reports_open",
            "thread_id", "reporter_type", "reporter_id", "reason",
            unique=True, postgresql_where=text("status = 'new'"),
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    thread_id: Mapped[UUID] = mapped_column(
        PGUUID,
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    reporter_type: Mapped[str] = mapped_column(String(8), nullable=False)
    reporter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="new", server_default=text("'new'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PartnerOffer(Base):
    """Лот в витрине внутреннего рынка: партнёр + заметка (§1, §3).

    `listing_id` UNIQUE — лот может быть в витрине только у одного партнёра;
    продавец в seller-чате берётся отсюда (`partner_id_for_listing`).
    """

    __tablename__ = "partner_offers"
    __table_args__ = (
        Index(
            "idx_partner_offers_partner_created", "partner_id", "created_at"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="CASCADE"), nullable=False,
    )
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True,
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
