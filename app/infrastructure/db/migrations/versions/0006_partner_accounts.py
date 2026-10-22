"""Партнёрский контур (P2-11): partner_accounts, partner_api_keys, лиды для CPL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "partner_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("contact", sa.Text(), nullable=True),
        sa.Column("plan", sa.Text(), nullable=False, server_default="starter"),
        sa.Column("daily_quota", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "partner_api_keys",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "partner_id", sa.BigInteger(),
            sa.ForeignKey("partner_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_partner_api_keys_hash"),
    )
    op.create_index(
        "idx_partner_api_keys_partner", "partner_api_keys", ["partner_id"]
    )

    # Лиды: эксклюзивная выдача партнёру + AI-квалификация (бюджет/срок/предоплата)
    op.add_column(
        "leads",
        sa.Column(
            "partner_id", sa.BigInteger(),
            sa.ForeignKey("partner_accounts.id"), nullable=True,
        ),
    )
    op.add_column("leads", sa.Column("partner_status", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("qualification", pg.JSONB(), nullable=True))
    op.create_index("idx_leads_partner", "leads", ["partner_id"])
    op.create_index("idx_leads_pool", "leads", ["status", "partner_id"])


def downgrade() -> None:
    op.drop_index("idx_leads_pool", table_name="leads")
    op.drop_index("idx_leads_partner", table_name="leads")
    op.drop_column("leads", "qualification")
    op.drop_column("leads", "claimed_at")
    op.drop_column("leads", "partner_status")
    op.drop_column("leads", "partner_id")
    op.drop_index("idx_partner_api_keys_partner", table_name="partner_api_keys")
    op.drop_table("partner_api_keys")
    op.drop_table("partner_accounts")