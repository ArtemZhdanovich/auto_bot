"""Engine_volume и color в listings (обогащение из detail encar).

Detail API encar (/v1/readside/vehicle/{id}) отдаёт spec.displacement (см³)
и spec.colorName — их нет в list-API, поэтому колонки заполняет enrichment
(лениво на PDP и при парсинге). Фильтры «объём от/до» и «цвет» — как на
avtokorea.by. engine_volume — литры (1995 см³ → 2.0).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("engine_volume", sa.Float()))
    op.add_column("listings", sa.Column("color", sa.Text()))


def downgrade() -> None:
    op.drop_column("listings", "color")
    op.drop_column("listings", "engine_volume")
