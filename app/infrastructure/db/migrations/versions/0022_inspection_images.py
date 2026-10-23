"""0022: inspection_reports.images — фото чека осмотра (днище/салон).

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Фото чека из анонимного API Encar — в отчёте осмотра, не в галерее."""
    op.add_column(
        "inspection_reports",
        sa.Column(
            "images",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("inspection_reports", "images")
