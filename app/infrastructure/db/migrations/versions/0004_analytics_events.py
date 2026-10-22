"""analytics_events — события продуктовой воронки (P1-7)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("props", pg.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_analytics_name_created", "analytics_events", ["name", "created_at"])
    op.create_index("idx_analytics_user_name", "analytics_events", ["user_id", "name"])


def downgrade() -> None:
    op.drop_index("idx_analytics_user_name", table_name="analytics_events")
    op.drop_index("idx_analytics_name_created", table_name="analytics_events")
    op.drop_table("analytics_events")