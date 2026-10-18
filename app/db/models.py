"""ORM-модели. Схема соответствует ТЗ п. 5.8 (основные таблицы)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
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


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    language_code: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, default="buyer")  # buyer / dealer / salon / admin
    subscription_tier: Mapped[str] = mapped_column(Text, default="free")
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Веб-регистрация (сайт): email+пароль; telegram_id синтетический (< 0),
    # уведомления — только через привязанный Telegram (см. /start ref_ deep link).
    email: Mapped[str | None] = mapped_column(Text, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    # Онбординг Mini App пройден (UI/UX ТЗ §2.8); NULL — ещё не проходил.
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserFilter(Base):
    __tablename__ = "filters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"))
    name: Mapped[str | None] = mapped_column(Text)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    notify_mode: Mapped[str] = mapped_column(Text, default="digest")  # instant / digest / off
    notify_times: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # ["09:00", "20:00"]
    quiet_hours: Mapped[str | None] = mapped_column(Text)  # "23:00-08:00"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(Base):
    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    adapter_class: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # api / parser / hybrid
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    schedule_cron: Mapped[str | None] = mapped_column(Text)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=60)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=3)
    timeout_sec: Mapped[int] = mapped_column(Integer, default=30)
    retry_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    credentials_ref: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceHealth(Base):
    __tablename__ = "source_health"

    source_name: Mapped[str] = mapped_column(ForeignKey("sources.name"), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    avg_latency_ms: Mapped[int | None] = mapped_column(Integer)
    total_fetched: Mapped[int] = mapped_column(BigInteger, default=0)


class SentLog(Base):
    __tablename__ = "sent_log"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Тип уведомления (миграция 0010): new / price_drop / gone / returned
    kind: Mapped[str] = mapped_column(String(16), primary_key=True, default="new")
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DialogSession(Base):
    __tablename__ = "dialog_sessions"

    id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    turn_count: Mapped[int | None] = mapped_column(Integer)
    messages: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Последняя активность диалога (миграция 0016): cron сносит сессии,
    # молчащие сутки (ТЗ: «диалоги живут сутки с последнего сообщения»).
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"))
    amount: Mapped[float] = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(Text)  # stars / yookassa / crypto
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(Text)
    period_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Favorite(Base):
    """Избранное пользователя (ТЗ п. 4.3: POST /api/listings/{id}/favorite)."""

    __tablename__ = "favorites"
    __table_args__ = (PrimaryKeyConstraint("user_id", "listing_id"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"))
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    source: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="new")  # new / in_progress / closed / rejected
    manager_id: Mapped[int | None] = mapped_column(BigInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    # Партнёрский контур (P2-10): эксклюзивная продажа лида партнёру (ИП/перекуп)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partner_accounts.id"))
    partner_status: Mapped[str | None] = mapped_column(
        Text
    )  # claimed / accepted / returned / junk / deal
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # AI-квалификация (бюджет/срок/готовность к предоплате) — поднимает цену лида
    qualification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_listings_source_source_id"),
        Index("idx_listings_make_model", "make", "model"),
        Index("idx_listings_year", "year"),
        Index("idx_listings_price", "price"),
        Index("idx_listings_vin", "vin"),
        # Отслеживание (миграция 0009)
        Index("idx_listings_last_seen", "last_seen_at"),
        Index(
            "idx_listings_active",
            "is_active",
            postgresql_where=text("is_active = TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    make: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    price: Mapped[float | None] = mapped_column(Numeric)
    currency: Mapped[str | None] = mapped_column(Text)
    mileage_km: Mapped[int | None] = mapped_column(Integer)
    vin: Mapped[str | None] = mapped_column(Text)
    # Спеки (этап 5, миграция 0016): кузов/топливо/КПП/привод — для фильтров
    # поиска и карточек. Канон — normalize_* из adapters.common.normalize;
    # нераспознанное значение сохраняется как есть (данные не теряем).
    body_type: Mapped[str | None] = mapped_column(Text)
    fuel: Mapped[str | None] = mapped_column(Text)
    transmission: Mapped[str | None] = mapped_column(Text)
    drive: Mapped[str | None] = mapped_column(Text)
    # Объём двигателя (литры, 1995 см³ → 2.0) и цвет — только в detail encar
    # (миграция 0017): заполняет enrichment, фильтры «объём от/до» и «цвет».
    engine_volume: Mapped[float | None] = mapped_column(Float)
    color: Mapped[str | None] = mapped_column(Text)
    images: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    # Сырой ответ адаптера живёт в S3/MinIO (PLAN.md, фаза 6, миграция 0012):
    # ключ объекта; NULL = не загружен (MinIO недоступен / флаг выключен).
    raw_key: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


    # Отслеживание наличия и изменений (PLAN.md, миграция 0009)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    disappeared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Перевод тайтла (PLAN.md, миграция 0011, фаза 5): title_raw — оригинал
    # с источника, title_ru — перевод (NLLB, services/translate);
    # смена content_hash — триггер повторного перевода.
    title_raw: Mapped[str | None] = mapped_column(Text)
    title_ru: Mapped[str | None] = mapped_column(Text)
    source_language: Mapped[str | None] = mapped_column(String(16))
    translated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ListingPriceHistory(Base):
    """Точка цены: строка только при изменении (PLAN.md, миграция 0009).

    Подготовка к партиционированию (миграция 0013, PLAN фаза 7): PK —
    (id, recorded_at), т.к. ключ будущего RANGE (recorded_at) обязан входить
    в PK; recorded_at NOT NULL. Таблица пока НЕ партиционирована.
    """

    __tablename__ = "listing_price_history"
    __table_args__ = (Index("idx_price_history_listing", "listing_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[float] = mapped_column(Numeric, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(8))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        primary_key=True,
    )


class AnalyticsEvent(Base):
    """Событие продуктовой воронки (P1-7): регистрация, поиск, диалог, оплата.

    Только факты для воронок (активация, retention, конверсии) — без ПД в props.
    """

    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("idx_analytics_name_created", "name", "created_at"),
        Index("idx_analytics_user_name", "user_id", "name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    props: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PriceAnalysis(Base):
    """AI-разбор цены объявления (монетизация: 1/нед бесплатно, далее подписка).

    Кэш общий на листинг (LLM дорогой — не дублируем на каждого юзера),
    квота считается по (user_id, listing_id, окно 7 дней).
    """

    __tablename__ = "price_analyses"
    __table_args__ = (
        Index("idx_price_analyses_listing_created", "listing_id", "created_at"),
        Index("idx_price_analyses_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)  # deal/fair/overpriced
    fair_min: Mapped[float | None] = mapped_column(Numeric)
    fair_max: Mapped[float | None] = mapped_column(Numeric)
    summary: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PartnerAccount(Base):
    """Партнёр (ИП/перекуп/салон), покупающий лиды (P2-10, CPL-монетизация).

    plan: starter / pro / salon — влияет на дневную квоту и цену лида.
    status: active / suspended — suspended блокирует все запросы по ключам.
    """

    __tablename__ = "partner_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    contact: Mapped[str | None] = mapped_column(Text)  # telegram / email
    plan: Mapped[str] = mapped_column(Text, default="starter")
    daily_quota: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(Text, default="active")
    # Биллинг (P2-14): CPL-списания с баланса; 0/недостаточно → приостановка
    balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    cpl_rate: Mapped[float | None] = mapped_column(
        Numeric(10, 2)
    )  # NULL = тариф плана
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PartnerTransaction(Base):
    """Журнал биллинга партнёра (P2-14): пополнения и CPL-списания.

    amount: +пополнение / −списание; balance_after — аудит-трейл.
    """

    __tablename__ = "partner_transactions"
    __table_args__ = (
        Index("idx_partner_tx_partner_created", "partner_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partner_accounts.id")
    )
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    kind: Mapped[str] = mapped_column(Text)  # topup / charge_lead / adjustment
    balance_after: Mapped[float] = mapped_column(Numeric(12, 2))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PartnerApiKey(Base):
    """API-ключ партнёра. Хранится только SHA-256 хэш + префикс для опознания.

    Ротация: старому ключу ставится status=rotated (мгновенно недействителен),
    выдаётся новый. status: active / rotated / revoked.
    """

    __tablename__ = "partner_api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_partner_api_keys_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partner_accounts.id"))
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(Text)  # "pi_ab12cd…" для ЛК
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminAction(Base):
    """Audit log админ-действий (UI/UX ТЗ §3.9, миграция 0008).

    Пишем кто (actor), что (action), над чем (entity/entity_id), payload и
    результат. Секреты (API-ключи) в payload НЕ попадают.
    """

    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(Text, default="admin")
    action: Mapped[str] = mapped_column(Text, nullable=False)  # partner.create / partner.rotate_key / ...
    entity: Mapped[str | None] = mapped_column(Text)  # "partner" / "source" / "lead" ...
    entity_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str | None] = mapped_column(Text)  # "ok" / текст ошибки
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InspectionReport(Base):
    """Отчёт осмотра (миграция 0018): карта повреждений + чек-лист.

    Один отчёт на объявление. marks/items — JSONB-списки (схема гибкая,
    валидируется на уровне Pydantic-схем API, не БД).
    """

    __tablename__ = "inspection_reports"
    __table_args__ = (Index("idx_inspection_reports_listing", "listing_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    source: Mapped[str] = mapped_column(Text, default="manual")  # manual / encar
    inspector: Mapped[str | None] = mapped_column(Text)
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grade: Mapped[str | None] = mapped_column(Text)  # excellent / good / fair / poor
    summary: Mapped[str | None] = mapped_column(Text)
    marks: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # [{zone, type, note}]
    items: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # [{key, status, note}]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
