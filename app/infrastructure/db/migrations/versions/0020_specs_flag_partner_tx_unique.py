"""0020: specs_enriched_at (3.7), uq_partner_tx_lead_kind (2.4), dialog id default (3.6).

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Флаг обогащения спеков + уникальность списаний за лид + UUID-дефолт."""
    # 3.7: одна попытка enrichment на лот.
    op.add_column(
        "listings",
        sa.Column("specs_enriched_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 2.4: двойное списание charge_lead за один лид невозможно.
    op.create_index(
        "uq_partner_tx_lead_kind",
        "partner_transactions",
        ["lead_id", "kind"],
        unique=True,
        postgresql_where=sa.text("lead_id IS NOT NULL"),
    )
    # 3.6: inserts без явного id (raw SQL/сидеры) получают UUID из БД.
    op.alter_column(
        "dialog_sessions",
        "id",
        server_default=sa.text("gen_random_uuid()"),
    )


def downgrade() -> None:
    op.alter_column("dialog_sessions", "id", server_default=None)
    op.drop_index("uq_partner_tx_lead_kind", table_name="partner_transactions")
    op.drop_column("listings", "specs_enriched_at")
