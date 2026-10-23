"""0023: listings.options — комплектация (русифицированные опции источника).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Коды опций из detail-карточки Encar резолвим в RU-названия адаптером."""
    op.add_column(
        "listings",
        sa.Column(
            "options",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("listings", "options")
