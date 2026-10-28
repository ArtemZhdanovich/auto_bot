"""Владелец партнёрского счёта: owner_telegram_id → Business (прод-ревизия 2026-10).

Клиенты/сотрудники партнёра получают тариф Business автоматически
(`subscription_until IS NULL` — бессрочно, пока партнёр активен).
Поиск владельца идёт по telegram_id — нужен уникальный индекс.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "partner_accounts",
        sa.Column(
            "owner_telegram_id",
            sa.BigInteger(),
            nullable=True,
            comment="Владелец счёта: получает Business, пока партнёр активен",
        ),
    )
    op.create_index(
        "uq_partner_accounts_owner_telegram_id",
        "partner_accounts",
        ["owner_telegram_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_partner_accounts_owner_telegram_id", table_name="partner_accounts"
    )
    op.drop_column("partner_accounts", "owner_telegram_id")
