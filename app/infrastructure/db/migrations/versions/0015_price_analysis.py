"""AI-разбор цены: таблица price_analyses (кэш разбора + квоты).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_analyses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "listing_id", sa.BigInteger(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("fair_min", sa.Numeric()),
        sa.Column("fair_max", sa.Numeric()),
        sa.Column("summary", sa.Text()),
        sa.Column("payload", JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "idx_price_analyses_listing_created",
        "price_analyses", ["listing_id", "created_at"],
    )
    op.create_index(
        "idx_price_analyses_user_created", "price_analyses", ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("price_analyses")
