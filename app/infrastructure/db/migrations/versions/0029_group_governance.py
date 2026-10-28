"""Говернанс переговорных комнат (08 §2): роли, mute, аудит, жалобы.

docs/REDESIGN/08-group-governance.md:
- `chat_participants.role` VARCHAR(8) NOT NULL DEFAULT 'member'
  CHECK IN ('owner','member') — создатель group-треда 'owner', вошедшие
  по инвайт-коду 'member'. Существующие строки создателей group-тредов
  бэкфиллим в 'owner' (§2: владелец = создатель переговорной);
- `chat_participants.muted` BOOLEAN NOT NULL DEFAULT false — свои
  уведомления (§6: muted-тред не инкрементит бейдж конверта);
- `chat_threads.closed_at` TIMESTAMPTZ NULL — момент закрытия (архив §2
  или пустой тред после ухода последнего участника);
- таблица `chat_events` — аудит-лента операций (§5): created / joined /
  left / invited / kicked / archived / reported / muted / unmuted,
  actor — инициатор (user/partner/operator), payload JSONB;
- таблица `chat_reports` — жалобы участников (§4, reason: spam | fraud |
  offtop | abuse), идемпотентность одного «открытого» репорта — частичный
  unique (thread_id, reporter_type, reporter_id, reason) WHERE
  status='new' (после обработки можно пожаловаться повторно).

Retention (§5): закрытые треды старше 90 дней скрываются ФИЛЬТРОМ в
выборках списков (GET /api/chats и партнёрский список — см.
SqlAlchemyChatRepository.list_threads_for_*), cron НЕ нужен: строки
остаются в БД (юр. аудит и лента событий не теряются).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Роли участников (§2): 'member' по умолчанию, создатель — 'owner'.
    op.add_column(
        "chat_participants",
        sa.Column(
            "role",
            sa.String(length=8),
            server_default=sa.text("'member'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "role_valid",
        "chat_participants",
        "role IN ('owner','member')",
    )
    # Свои уведомления (§6): muted-тред не дёргает бейдж конверта.
    op.add_column(
        "chat_participants",
        sa.Column(
            "muted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    # Бэкфилл: создатели существующих group-тредов становятся owner —
    # иначе после DEFAULT 'member' они теряли право invite/archive (§2).
    op.execute(
        "UPDATE chat_participants cp SET role = 'owner' "
        "FROM chat_threads ct "
        "WHERE cp.thread_id = ct.id AND ct.type = 'group' "
        "AND cp.user_type = 'user' AND cp.user_id = ct.user_id"
    )
    # Момент закрытия треда — точка отсчёта 90-дневного retention (§5).
    op.add_column(
        "chat_threads",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "chat_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", postgresql.UUID(), nullable=False),
        sa.Column("actor_type", sa.String(length=8), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["chat_threads.id"],
            name=op.f("fk_chat_events_thread_id_chat_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_events")),
        sa.CheckConstraint(
            "event IN ('created','joined','left','invited','kicked',"
            "'archived','reported','muted','unmuted')",
            name="event_valid",
        ),
    )
    op.create_index(
        "idx_chat_events_thread_id",
        "chat_events",
        ["thread_id", "id"],
    )
    op.create_index(
        "idx_chat_events_event_created",
        "chat_events",
        ["event", "created_at"],
    )

    op.create_table(
        "chat_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", postgresql.UUID(), nullable=False),
        sa.Column("reporter_type", sa.String(length=8), nullable=False),
        sa.Column("reporter_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'new'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["chat_threads.id"],
            name=op.f("fk_chat_reports_thread_id_chat_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_reports")),
        sa.CheckConstraint(
            "reason IN ('spam','fraud','offtop','abuse')", name="reason_valid"
        ),
        sa.CheckConstraint(
            "status IN ('new','resolved','dismissed')", name="status_valid"
        ),
    )
    # Идемпотентность «открытой» жалобы: один reporter+reason на тред,
    # пока не обработана (после resolved/dismissed — можно снова, §4).
    op.create_index(
        "uq_chat_reports_open",
        "chat_reports",
        ["thread_id", "reporter_type", "reporter_id", "reason"],
        unique=True,
        postgresql_where=sa.text("status = 'new'"),
    )


def downgrade() -> None:
    op.drop_index("uq_chat_reports_open", table_name="chat_reports")
    op.drop_table("chat_reports")
    op.drop_index("idx_chat_events_event_created", table_name="chat_events")
    op.drop_index("idx_chat_events_thread_id", table_name="chat_events")
    op.drop_table("chat_events")
    op.drop_column("chat_threads", "closed_at")
    op.drop_column("chat_participants", "muted")
    op.drop_column("chat_participants", "role")
