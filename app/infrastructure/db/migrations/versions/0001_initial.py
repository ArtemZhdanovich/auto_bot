"""initial schema (ТЗ п. 5.8)

Revision ID: 0001
Revises:
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.Text()),
        sa.Column("first_name", sa.Text()),
        sa.Column("last_name", sa.Text()),
        sa.Column("language_code", sa.Text()),
        sa.Column("role", sa.Text(), server_default="buyer"),
        sa.Column("subscription_tier", sa.Text(), server_default="free"),
        sa.Column("subscription_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true()),
    )

    op.create_table(
        "filters",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id")),
        sa.Column("name", sa.Text()),
        sa.Column("criteria", pg.JSONB(), nullable=False),
        sa.Column("notify_mode", sa.Text(), server_default="digest"),
        sa.Column("notify_times", pg.ARRAY(sa.Text())),
        sa.Column("quiet_hours", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "listings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("make", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("year", sa.Integer()),
        sa.Column("price", sa.Numeric()),
        sa.Column("currency", sa.Text()),
        sa.Column("mileage_km", sa.Integer()),
        sa.Column("vin", sa.Text()),
        sa.Column("images", pg.JSONB(), server_default="[]"),
        sa.Column("raw_payload", pg.JSONB()),
        sa.Column("schema_version", sa.Integer(), server_default="1"),
        sa.Column("parsed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source", "source_id", name="uq_listings_source_source_id"),
    )
    op.create_index("idx_listings_make_model", "listings", ["make", "model"])
    op.create_index("idx_listings_year", "listings", ["year"])
    op.create_index("idx_listings_price", "listings", ["price"])
    op.create_index("idx_listings_vin", "listings", ["vin"])

    op.create_table(
        "sources",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("adapter_class", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.Column("priority", sa.Integer(), server_default="100"),
        sa.Column("schedule_cron", sa.Text()),
        sa.Column("rate_limit_per_min", sa.Integer(), server_default="60"),
        sa.Column("max_concurrency", sa.Integer(), server_default="3"),
        sa.Column("timeout_sec", sa.Integer(), server_default="30"),
        sa.Column("retry_policy", pg.JSONB()),
        sa.Column("credentials_ref", sa.Text()),
        sa.Column("capabilities", pg.JSONB()),
        sa.Column("config", pg.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "source_health",
        sa.Column(
            "source_name",
            sa.Text(),
            sa.ForeignKey("sources.name"),
            primary_key=True,
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("error_streak", sa.Integer(), server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("avg_latency_ms", sa.Integer()),
        sa.Column("total_fetched", sa.BigInteger(), server_default="0"),
    )

    op.create_table(
        "sent_log",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("listing_id", sa.BigInteger(), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "dialog_sessions",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("criteria", pg.JSONB(), nullable=False),
        sa.Column("turn_count", sa.Integer()),
        sa.Column("messages", pg.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id")),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text()),
        sa.Column("provider_payment_id", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("tier", sa.Text()),
        sa.Column("period_days", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id")),
        sa.Column("listing_id", sa.BigInteger(), sa.ForeignKey("listings.id")),
        sa.Column("source", sa.Text()),
        sa.Column("status", sa.Text(), server_default="new"),
        sa.Column("manager_id", sa.BigInteger()),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("leads")
    op.drop_table("payments")
    op.drop_table("dialog_sessions")
    op.drop_table("sent_log")
    op.drop_table("source_health")
    op.drop_table("sources")
    op.drop_index("idx_listings_vin", table_name="listings")
    op.drop_index("idx_listings_price", table_name="listings")
    op.drop_index("idx_listings_year", table_name="listings")
    op.drop_index("idx_listings_make_model", table_name="listings")
    op.drop_table("listings")
    op.drop_table("filters")
    op.drop_table("users")
