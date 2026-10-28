"""Жизненный цикл объявления рынка: статусы модерации (редизайн часть 10).

docs/REDESIGN/10-listing-lifecycle.md §1:
- `listings.status` VARCHAR(16) NOT NULL DEFAULT 'accepted' CHECK IN
  ('draft','moderation','accepted','rejected','archived','expired') —
  конечный автомат owner-лотов (draft → moderation → accepted → …).
  Внешние лоты (encar/kcar/che168) живут с 'accepted' — серверный
  default закрывает вставки парсера, минуя модерацию;
- `listings.rejected_reason` TEXT NULL — причина отказа (rule-детали
  авто-модерации или текст администратора);
- `listings.moderation_payload` JSONB NULL — сработавшие правила и
  risk_score последнего скрининга ({violations: [...], risk_score: int});
- `listings.pending_patch` JSONB NULL — версия-на-модерации при правке
  опубликованного лота (NOT NULL = патч на проверке; отклонение патча
  дописывает `patch_reject_reason` внутрь JSON).

Backfill (§7):
- внешние лоты (owner_type IS NULL) → 'accepted' — их видимость не меняется;
- owner-лоты: is_active=TRUE и placement живой/включён (без дат) →
  'accepted'; остальное (needs_payment без дат, снятые, истёкшие) →
  'draft' — владелец дозаполнит и отправит на модерацию заново.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listings", sa.Column("status", sa.String(length=16), nullable=True)
    )
    op.create_check_constraint(
        "listing_status_valid",
        "listings",
        "status IS NULL OR status IN "
        "('draft','moderation','accepted','rejected','archived','expired')",
    )
    # Backfill §1/§7: внешние лоты всегда видны — accepted.
    op.execute(
        "UPDATE listings SET status = 'accepted' WHERE owner_type IS NULL"
    )
    # Активные owner-лоты с живым placement (даты в будущем) или включённые
    # (без дат — лот платного тарифа) остаются опубликованными.
    op.execute(
        "UPDATE listings SET status = 'accepted' "
        "WHERE owner_type IS NOT NULL AND is_active = TRUE "
        "AND (placement_free_until > now() OR placement_paid_until > now() "
        "OR (placement_free_until IS NULL AND placement_paid_until IS NULL))"
    )
    # Остальные owner-лоты (needs_payment-ждут оплаты, снятые, истёкшие) —
    # draft: публикация теперь только через submit → модерацию (§4).
    op.execute("UPDATE listings SET status = 'draft' WHERE status IS NULL")
    # NOT NULL + default 'accepted': вставки парсера внешних лотов без
    # явного статуса остаются видимыми (совместимость с парсерами).
    op.alter_column(
        "listings",
        "status",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default=sa.text("'accepted'"),
    )
    op.add_column(
        "listings", sa.Column("rejected_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "listings",
        sa.Column("moderation_payload", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "listings", sa.Column("pending_patch", postgresql.JSONB(), nullable=True)
    )
    # Очередь ручной модерации (GET /api/admin/moderation) — частичные
    # индексы: строк в очереди на порядки меньше полной таблицы.
    op.create_index("idx_listings_status", "listings", ["status"])
    op.create_index(
        "idx_listings_moderation_queue",
        "listings",
        ["id"],
        postgresql_where=sa.text("status = 'moderation'"),
    )
    op.create_index(
        "idx_listings_pending_patch",
        "listings",
        ["id"],
        postgresql_where=sa.text("pending_patch IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_listings_pending_patch", table_name="listings")
    op.drop_index("idx_listings_moderation_queue", table_name="listings")
    op.drop_index("idx_listings_status", table_name="listings")
    op.drop_column("listings", "pending_patch")
    op.drop_column("listings", "moderation_payload")
    op.drop_column("listings", "rejected_reason")
    op.alter_column(
        "listings",
        "status",
        existing_type=sa.String(length=16),
        nullable=True,
        server_default=None,
    )
    op.drop_constraint("listing_status_valid", "listings")
    op.drop_column("listings", "status")
