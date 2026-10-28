"""Чаты (support/seller/deal) + участники + витрина внутреннего рынка.

Редизайн часть 5 (docs/REDESIGN/05-chats-marketplace.md §3): таблицы
chat_threads / chat_messages / chat_participants / partner_offers.
Уникальность треда по предмету — частичные unique-индексы (reuse вместо
конфликтов). Имена каналов centrifugo = chat_threads.id (uuid).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_threads",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=True),
        sa.Column("listing_id", sa.BigInteger(), nullable=True),
        sa.Column("lead_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "bot_paused",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_threads")),
        sa.CheckConstraint("status IN ('active','closed')", name="status_valid"),
        sa.CheckConstraint("type IN ('support','seller','deal')", name="type_valid"),
    )
    op.create_index(
        "idx_chat_threads_partner_last",
        "chat_threads",
        ["partner_id", "last_message_at"],
    )
    op.create_index(
        "idx_chat_threads_user_last",
        "chat_threads",
        ["user_id", "last_message_at"],
    )
    # Частичные unique-срезы: singleton поддержки, seller на (юзер,лот), deal на лид.
    op.create_index(
        "uq_chat_threads_support",
        "chat_threads",
        ["type", "user_id"],
        unique=True,
        postgresql_where=sa.text("type = 'support'"),
    )
    op.create_index(
        "uq_chat_threads_seller",
        "chat_threads",
        ["type", "user_id", "listing_id"],
        unique=True,
        postgresql_where=sa.text(
            "type = 'seller' AND listing_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_chat_threads_deal",
        "chat_threads",
        ["type", "lead_id"],
        unique=True,
        postgresql_where=sa.text("type = 'deal' AND lead_id IS NOT NULL"),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", postgresql.UUID(), nullable=False),
        sa.Column("sender_type", sa.String(16), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["chat_threads.id"],
            name=op.f("fk_chat_messages_thread_id_chat_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
        sa.CheckConstraint(
            "sender_type IN ('user','operator','partner','bot','system')",
            name="sender_type_valid",
        ),
    )
    op.create_index(
        "idx_chat_messages_thread_desc",
        "chat_messages",
        ["thread_id", sa.text("id DESC")],
    )

    op.create_table(
        "chat_participants",
        sa.Column("thread_id", postgresql.UUID(), nullable=False),
        sa.Column("user_type", sa.String(16), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "last_read_message_id",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["chat_threads.id"],
            name=op.f("fk_chat_participants_thread_id_chat_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "thread_id", "user_type", "user_id", name=op.f("pk_chat_participants")
        ),
        sa.CheckConstraint(
            "user_type IN ('user','operator','partner')", name="user_type_valid"
        ),
    )
    op.create_index("idx_chat_participants_user", "chat_participants", ["user_id"])

    op.create_table(
        "partner_offers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_partner_offers_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["partner_accounts.id"],
            name=op.f("fk_partner_offers_partner_id_partner_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_partner_offers")),
        sa.UniqueConstraint(
            "listing_id", name=op.f("uq_partner_offers_listing_id")
        ),
    )
    op.create_index(
        "idx_partner_offers_partner_created",
        "partner_offers",
        ["partner_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_partner_offers_partner_created", table_name="partner_offers"
    )
    op.drop_table("partner_offers")
    op.drop_index("idx_chat_participants_user", table_name="chat_participants")
    op.drop_table("chat_participants")
    op.drop_index(
        "idx_chat_messages_thread_desc", table_name="chat_messages"
    )
    op.drop_table("chat_messages")
    op.drop_index("uq_chat_threads_deal", table_name="chat_threads")
    op.drop_index("uq_chat_threads_seller", table_name="chat_threads")
    op.drop_index("uq_chat_threads_support", table_name="chat_threads")
    op.drop_index("idx_chat_threads_user_last", table_name="chat_threads")
    op.drop_index("idx_chat_threads_partner_last", table_name="chat_threads")
    op.drop_table("chat_threads")
