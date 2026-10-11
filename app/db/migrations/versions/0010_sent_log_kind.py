"""sent_log.kind: тип уведомления (new/price_drop/gone/returned) — PLAN.md, фаза 3.

PK расширяется до (user_id, listing_id, kind): одно объявление может быть
отправлено пользователю несколько раз — как new и позже как price_drop.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sent_log",
        sa.Column("kind", sa.String(16), nullable=False, server_default="new"),
    )
    op.drop_constraint("sent_log_pkey", "sent_log", type_="primary")
    op.create_primary_key(
        "sent_log_pkey", "sent_log", ["user_id", "listing_id", "kind"]
    )


def downgrade() -> None:
    op.drop_constraint("sent_log_pkey", "sent_log", type_="primary")
    op.create_primary_key("sent_log_pkey", "sent_log", ["user_id", "listing_id"])
    op.drop_column("sent_log", "kind")
