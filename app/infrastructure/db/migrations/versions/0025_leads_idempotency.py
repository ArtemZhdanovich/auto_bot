"""Корректирующая миграция: leads.idempotency_key (схема-дрейф, прод 2026-10).

Колонка давно живёт в ORM (models.Lead.idempotency_key + partial unique
index) и используется сценарием создания лида, но миграции для неё не
было — на живой БД POST /api/listings/{id}/agent падал с
ProgrammingError (column leads.idempotency_key does not exist).

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("idempotency_key", sa.String(255), nullable=True),
    )
    op.create_index(
        "uq_leads_idempotency_key",
        "leads",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_leads_idempotency_key", table_name="leads")
    op.drop_column("leads", "idempotency_key")
