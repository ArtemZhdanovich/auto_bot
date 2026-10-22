"""Перевод тайтлов: title_raw/title_ru, source_language, translated_at — PLAN.md, фаза 5.

listings.title в схеме нет: оригинальный тайтл до сих пор жил только в
UnifiedListing/content_hash. title_raw — оригинал с источника (бэкфилл из
парсинга не нужен: колонка новая, заполняется upsert'ом), title_ru — перевод
(NLLB, сервис services/translate), source_language — NLLB-код исходника
(kor_Hang/zho_Hans/...), translated_at — время перевода. Смена content_hash —
триггер повторного перевода (см. save_listings → translate_ids).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("title_raw", sa.Text()))
    op.add_column("listings", sa.Column("title_ru", sa.Text()))
    op.add_column("listings", sa.Column("source_language", sa.String(16)))
    op.add_column(
        "listings", sa.Column("translated_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("listings", "translated_at")
    op.drop_column("listings", "source_language")
    op.drop_column("listings", "title_ru")
    op.drop_column("listings", "title_raw")