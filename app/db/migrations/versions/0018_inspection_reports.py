"""Отчёты осмотра (карта повреждений + чек-лист) — таблица inspection_reports.

Карта повреждений как на avtokorea.by: зоны кузова с типами дефектов
(marks) + чек-лист агрегатов со статусами (items) + общая оценка (grade).
Один отчёт на объявление (unique listing_id). Источник: manual — ручной
осмотр; encar — слот под будущий парсинг диагностики encar.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inspection_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("listing_id", sa.Integer(), nullable=False, unique=True),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="CASCADE"),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("inspector", sa.Text()),
        sa.Column("inspected_at", sa.DateTime(timezone=True)),
        # excellent / good / fair / poor
        sa.Column("grade", sa.Text()),
        sa.Column("summary", sa.Text()),
        # [{zone, type, note}] — карта повреждений
        sa.Column("marks", JSONB, nullable=False, server_default="[]"),
        # [{key, status, note}] — чек-лист агрегатов
        sa.Column("items", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_inspection_reports_listing", "inspection_reports", ["listing_id"])


def downgrade() -> None:
    op.drop_index("idx_inspection_reports_listing", table_name="inspection_reports")
    op.drop_table("inspection_reports")
