"""Биллинг партнёров (P2): баланс, CPL-тариф, журнал начислений.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "partner_accounts",
        sa.Column(
            "balance",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
            comment="Баланс в USD для CPL-списаний",
        ),
    )
    op.add_column(
        "partner_accounts",
        sa.Column(
            "cpl_rate",
            sa.Numeric(10, 2),
            nullable=True,
            comment="Индивидуальная цена лида; NULL = тариф плана",
        ),
    )

    op.create_table(
        "partner_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "partner_id", sa.BigInteger(),
            sa.ForeignKey("partner_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "lead_id", sa.BigInteger(),
            sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, comment="+пополнение, −списание"),
        sa.Column(
            "kind", sa.Text(), nullable=False,
            comment="topup / charge_lead / adjustment",
        ),
        sa.Column(
            "balance_after", sa.Numeric(12, 2), nullable=False,
            comment="Баланс после операции (аудит-трейл)",
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_partner_tx_partner_created",
        "partner_transactions",
        ["partner_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_partner_tx_partner_created", table_name="partner_transactions")
    op.drop_table("partner_transactions")
    op.drop_column("partner_accounts", "cpl_rate")
    op.drop_column("partner_accounts", "balance")
