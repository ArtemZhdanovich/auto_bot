"""add che168 source (Китай, SSR-парсер)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# enabled=false: включается владельцем после сверки селекторов с живым HTML
# и проверки гео-доступности (che168 может не отдавать контент с не-китайских IP).
INSERT_SQL = sa.text(
    """
    INSERT INTO sources
        (name, adapter_class, kind, enabled, priority, schedule_cron,
         rate_limit_per_min, timeout_sec, config, capabilities)
    VALUES (
        'che168', 'Che168HtmlAdapter', 'parser', false, 300, '*/30 * * * *',
        10, 30,
        :config::jsonb,
        :capabilities::jsonb
    )
    ON CONFLICT (name) DO NOTHING
    """
)
CONFIG = (
    '{"base_url": "https://www.che168.com", '
    '"list_path": "/china/a0_0msdgscncgpi1ltocsp1exb0/", '
    '"card_selector": "ul#usedList li", '
    '"title_selector": "h4", "price_selector": ".price", '
    '"link_selector": "a", "max_pages": 5}'
)
CAPABILITIES = '{"fuel_from_text": true, "transmission_from_text": true}'


def upgrade() -> None:
    op.execute(
        INSERT_SQL.bindparams(config=CONFIG, capabilities=CAPABILITIES)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM sources WHERE name = 'che168'"))
