"""0021: users.source (воронка miniapp) + idx_users_source_created + uq_users_email_lower.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Модель User (добавлена в 4d42fde) опередила миграции: догоняем БД."""
    # Источник создания: miniapp / bot / web / telegram_link. NULL — старые записи.
    op.add_column("users", sa.Column("source", sa.String(16), nullable=True))
    op.create_index("idx_users_source_created", "users", ["source", "created_at"])
    # Уникальность email case-insensitive — функциональный индекс, как в модели.
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_index("idx_users_source_created", table_name="users")
    op.drop_column("users", "source")
