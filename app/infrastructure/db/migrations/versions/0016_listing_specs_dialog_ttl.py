"""Спеки в listings (кузов/топливо/КПП/привод) + dialog_sessions.updated_at.

Спеки (этап 5): адаптеры уже отдают body_type/fuel/transmission/drive в
UnifiedListing, но в БД они не сохранялись — фильтры поиска и «тип кузова»
в карточках были пустыми. Колонки nullable: история заполняется при следующих
парсингах (UPSERT перепишет). dialog_sessions.updated_at — TTL чистки диалогов
«через сутки с последнего сообщения» (cron).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("body_type", sa.Text()))
    op.add_column("listings", sa.Column("fuel", sa.Text()))
    op.add_column("listings", sa.Column("transmission", sa.Text()))
    op.add_column("listings", sa.Column("drive", sa.Text()))
    op.add_column(
        "dialog_sessions",
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "idx_dialog_sessions_updated",
        "dialog_sessions",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_dialog_sessions_updated", table_name="dialog_sessions")
    op.drop_column("dialog_sessions", "updated_at")
    op.drop_column("listings", "drive")
    op.drop_column("listings", "transmission")
    op.drop_column("listings", "fuel")
    op.drop_column("listings", "body_type")
