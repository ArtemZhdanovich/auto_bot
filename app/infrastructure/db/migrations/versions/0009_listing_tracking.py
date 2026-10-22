"""Отслеживание объявлений: наличие, content_hash, история цен (PLAN.md, фазы 1–2).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column(
        "listings",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # first/last_seen: добавляем nullable, бэкфиллим из parsed_at, затем NOT NULL
    op.add_column("listings", sa.Column("first_seen_at", sa.DateTime(timezone=True)))
    op.add_column("listings", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.add_column("listings", sa.Column("disappeared_at", sa.DateTime(timezone=True)))
    op.add_column("listings", sa.Column("price_changed_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE listings SET first_seen_at = COALESCE(parsed_at, now()), "
        "last_seen_at = COALESCE(parsed_at, now())"
    )
    op.alter_column(
        "listings",
        "first_seen_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    op.alter_column(
        "listings",
        "last_seen_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

    op.create_table(
        "listing_price_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "listing_id",
            sa.BigInteger(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("currency", sa.String(8)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index(
        "idx_listings_active",
        "listings",
        ["is_active"],
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.create_index("idx_listings_last_seen", "listings", ["last_seen_at"])
    op.create_index(
        "idx_listings_first_seen_brin",
        "listings",
        ["first_seen_at"],
        postgresql_using="brin",
    )
    op.create_index(
        "idx_price_history_listing",
        "listing_price_history",
        [sa.text("listing_id"), sa.text("recorded_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_price_history_listing", table_name="listing_price_history")
    op.drop_index("idx_listings_first_seen_brin", table_name="listings")
    op.drop_index("idx_listings_last_seen", table_name="listings")
    op.drop_index("idx_listings_active", table_name="listings")
    op.drop_table("listing_price_history")
    for col in (
        "price_changed_at",
        "disappeared_at",
        "last_seen_at",
        "first_seen_at",
        "is_active",
        "content_hash",
    ):
        op.drop_column("listings", col)
