"""ORM-модели. Схема соответствует ТЗ п. 5.8 (основные таблицы)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
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
    images: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
