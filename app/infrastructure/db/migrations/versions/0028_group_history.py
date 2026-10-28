"""Переговорные чаты (group) + история машины лота (owner_history).

docs/REDESIGN/07-sell-chats-partner-inspection.md §2-§3:
- `chat_threads`: CHECK type расширяется до 'group' + колонка `title`
  VARCHAR(120) NULL — название переговорной задаёт юзер (для
  support/seller/deal остаётся NULL, заголовок генерится при выдаче);
- `listings.owner_history` JSONB NOT NULL DEFAULT '[]' — события истории
  машины партнёра «как в Корее» (`[{date, title, note?}]`, <=50),
  PDP-таймлайн.

Имя CHECK-констрейнта — фактическое из миграции 0026: в `create_table`
имя передано явно (`type_valid`), без naming-конвенции `ck_%(table)s_...`.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # chat_threads: type += 'group' (drop/recreate CHECK, имя из 0026).
    op.drop_constraint("type_valid", "chat_threads", type_="check")
    op.create_check_constraint(
        "type_valid",
        "chat_threads",
        "type IN ('support','seller','deal','group')",
    )
    op.add_column(
        "chat_threads",
        sa.Column("title", sa.String(length=120), nullable=True),
    )
    # listings: история машины (партнёрские чеки, §3).
    op.add_column(
        "listings",
        sa.Column(
            "owner_history",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    # Group-треды не живут под старым CHECK — удаляем каскадно
    # (сообщения/участники идут за тредом через ON DELETE CASCADE).
    op.execute("DELETE FROM chat_threads WHERE type = 'group'")
    op.drop_column("listings", "owner_history")
    op.drop_column("chat_threads", "title")
    op.drop_constraint("type_valid", "chat_threads", type_="check")
    op.create_check_constraint(
        "type_valid",
        "chat_threads",
        "type IN ('support','seller','deal')",
    )
