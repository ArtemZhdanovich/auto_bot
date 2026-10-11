"""Подготовка listing_price_history к партиционированию (PLAN.md, фаза 7).

Включение партиционирования отложено до триггера (1–2 млн строк), но схему
готовим заранее, пока таблица мала:

- PK (id) → (id, recorded_at): ключ партиционирования RANGE (recorded_at)
  обязан входить в PK — иначе будущая миграция потребует перестройки PK
  на миллионах строк;
- recorded_at NOT NULL: nullable-колонка не может быть ключом партиционирования
  (серверный default now() уже стоит, данные не трогаем).

Данные не переносятся, таблица НЕ партиционируется — только схема.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "listing_price_history",
        "recorded_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.execute(
        "ALTER TABLE listing_price_history DROP CONSTRAINT listing_price_history_pkey"
    )
    op.create_primary_key(
        "listing_price_history_pkey", "listing_price_history", ["id", "recorded_at"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "listing_price_history_pkey", "listing_price_history", type_="primary"
    )
    op.create_primary_key(
        "listing_price_history_pkey", "listing_price_history", ["id"]
    )
    op.alter_column(
        "listing_price_history",
        "recorded_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )