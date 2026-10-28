"""Гео-контур объявлений (GEO-V1 §2): country/city + бэкфилл по источникам.

docs/GEO-V1.md §2:
- `listings.country` VARCHAR(2) NULL — ISO-3166 alpha-2 (`KR`,`CN`,`AE`,
  `US`,`KZ`,`RU`, …); внешним лотам проставляет адаптер в
  `UnifiedListing.country`, owner-лотам рынка — форма продавца;
- `listings.city` VARCHAR(64) NULL — только для owner-лотов рынка;
- бэкфилл существующих строк по source-мапе (encar/kcar→KR, che168→CN,
  dubizzle→AE, cars_com→US, kolesa→KZ; прочие источники не трогаем —
  страна останется NULL до первого парсинга с `country`);
- индекс `ix_listings_country` — фильтр ленты `?country=` и фасет
  `countries` (distinct по is_active).

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Страна известных источников (GEO-V1 §1); новые адаптеры проставляют
# `country` сами — мапа нужна только для одного бэкфилла истории.
_SOURCE_COUNTRY: tuple[tuple[str, str], ...] = (
    ("encar", "KR"),
    ("kcar", "KR"),
    ("che168", "CN"),
    ("dubizzle", "AE"),
    ("cars_com", "US"),
    ("kolesa", "KZ"),
)


def upgrade() -> None:
    op.add_column("listings", sa.Column("country", sa.String(length=2), nullable=True))
    op.add_column("listings", sa.Column("city", sa.String(length=64), nullable=True))
    # Бэкфилл §2: страна известна по источнику; owner-лоты рынка (source=
    # 'market') и неизвестные источники остаются NULL — страну укажет
    # владелец/адаптер при следующей записи.
    for source, country in _SOURCE_COUNTRY:
        op.execute(
            f"UPDATE listings SET country = '{country}' "
            f"WHERE source = '{source}' AND country IS NULL"
        )
    op.create_index("ix_listings_country", "listings", ["country"])


def downgrade() -> None:
    op.drop_index("ix_listings_country", table_name="listings")
    op.drop_column("listings", "city")
    op.drop_column("listings", "country")
