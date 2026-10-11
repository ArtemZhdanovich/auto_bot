"""raw_payload → S3 (MinIO): listings.raw_key вместо JSONB — PLAN.md, фаза 6.

Сырые ответы адаптеров — «холодные» данные (аудит/отладка парсера): в
Postgres они раздували БД и бэкапы. Теперь ключ S3-объекта
`raw/{source}/{yyyy}/{mm}/{listing_id}_{content_hash}.json` пишется в
listings.raw_key (NULL = не загружен, например MinIO был недоступен);
сам JSON кладёт S3Storage (app/services/storage.py, aioboto3). Смена
content_hash → новый объект, дедупликация по хэшу бесплатна.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("listings", "raw_payload")
    op.add_column("listings", sa.Column("raw_key", sa.Text()))


def downgrade() -> None:
    op.drop_column("listings", "raw_key")
    op.add_column("listings", sa.Column("raw_payload", JSONB()))