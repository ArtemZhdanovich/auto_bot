"""Рынок с владельцами + монетизация размещений (редизайн часть 6).

docs/REDESIGN/06-market-user-listings.md §1:
- `listings`: owner_type ('user'|'partner') + owner_user_id/owner_partner_id
  (ровно один заполнен — два частичных CHECK), owner_description,
  placement_free_until/placement_paid_until (бесплатный/оплаченный период);
- `partner_accounts`: placement_slots/placement_slots_used (пакеты
  размещений партнёров);
- `payments`: kind ('subscription'|'placement') + listing_id — продление
  размещения лота юзера через Stars (контур payments, kind='placement');
- старые «операторские» seller-треды внешних лотов закрываются (§5):
  чат с продавцом теперь возможен только для лотов рынка с владельцем.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("owner_type", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "listings",
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "listings",
        sa.Column("owner_partner_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "listings",
        sa.Column("owner_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "listings",
        sa.Column(
            "placement_free_until", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "listings",
        sa.Column(
            "placement_paid_until", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_check_constraint(
        "owner_type_valid",
        "listings",
        "owner_type IS NULL OR owner_type IN ('user','partner')",
    )
    # Ровно один owner_id заполнен (§1): два частичных CHECK по типу.
    op.create_check_constraint(
        "owner_user_filled",
        "listings",
        "owner_type IS DISTINCT FROM 'user' "
        "OR (owner_user_id IS NOT NULL AND owner_partner_id IS NULL)",
    )
    op.create_check_constraint(
        "owner_partner_filled",
        "listings",
        "owner_type IS DISTINCT FROM 'partner' "
        "OR (owner_partner_id IS NOT NULL AND owner_user_id IS NULL)",
    )
    op.create_foreign_key(
        "fk_listings_owner_user_id_users",
        "listings",
        "users",
        ["owner_user_id"],
        ["telegram_id"],
    )
    op.create_foreign_key(
        "fk_listings_owner_partner_id_partner_accounts",
        "listings",
        "partner_accounts",
        ["owner_partner_id"],
        ["id"],
    )
    op.create_index(
        "idx_listings_owner",
        "listings",
        ["owner_type", "owner_user_id", "owner_partner_id"],
    )

    op.add_column(
        "partner_accounts",
        sa.Column(
            "placement_slots",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "partner_accounts",
        sa.Column(
            "placement_slots_used",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )

    op.add_column(
        "payments",
        sa.Column(
            "kind",
            sa.String(length=16),
            server_default=sa.text("'subscription'"),
            nullable=False,
        ),
    )
    op.add_column(
        "payments",
        sa.Column("listing_id", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "kind_valid",
        "payments",
        "kind IN ('subscription','placement')",
    )

    # §5: seller-треды внешних (encar/kcar) лотов без партнёра закрываются —
    # чат с «продавцом-парсером» экономически бессмысленен.
    op.execute(
        "UPDATE chat_threads SET status = 'closed' "
        "WHERE type = 'seller' AND partner_id IS NULL AND listing_id IN "
        "(SELECT id FROM listings WHERE owner_type IS NULL)"
    )


def downgrade() -> None:
    op.drop_constraint("kind_valid", "payments")
    op.drop_column("payments", "listing_id")
    op.drop_column("payments", "kind")
    op.drop_column("partner_accounts", "placement_slots_used")
    op.drop_column("partner_accounts", "placement_slots")
    op.drop_index("idx_listings_owner", table_name="listings")
    op.drop_constraint(
        "fk_listings_owner_partner_id_partner_accounts", "listings"
    )
    op.drop_constraint("fk_listings_owner_user_id_users", "listings")
    op.drop_constraint("owner_partner_filled", "listings")
    op.drop_constraint("owner_user_filled", "listings")
    op.drop_constraint("owner_type_valid", "listings")
    op.drop_column("listings", "placement_paid_until")
    op.drop_column("listings", "placement_free_until")
    op.drop_column("listings", "owner_description")
    op.drop_column("listings", "owner_partner_id")
    op.drop_column("listings", "owner_user_id")
    op.drop_column("listings", "owner_type")
