"""outbox event_id for idempotent platform delivery

Revision ID: b2e3d4f5a6c7
Revises: a1f2c3d4e5b6
Create Date: 2026-04-29 22:00:00.000000

Añade columna event_id (UUID) a las 4 tablas que reciben eventos del
bot via /api/bot/{trade,signal,marker,regime}. Junto con el outbox
del bot (V83 hl_platform_outbox), esto permite delivery garantizada
con idempotencia: el bot genera un UUID al enqueue, el platform usa
ON CONFLICT (event_id) DO NOTHING — un mismo event_id puede
reintentar N veces sin duplicar.

Multi-tenant safe: event_id es global UUID v4 (1 en 2^122 colisión),
las 4 tablas mantienen su user_id existente. Indices con WHERE event_id
IS NOT NULL para no penalizar las filas legacy (pre-V83) que llegaron
sin event_id — esas siguen siendo legítimas, solo no son idempotentes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2e3d4f5a6c7'
down_revision: Union[str, Sequence[str], None] = 'a1f2c3d4e5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = ['trade_outcomes', 'signal_evaluations', 'change_markers', 'regime_labels']


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column('event_id', sa.UUID(), nullable=True))
        op.create_index(
            f'uq_{table}_event_id',
            table,
            ['event_id'],
            unique=True,
            postgresql_where=sa.text('event_id IS NOT NULL'),
        )


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(f'uq_{table}_event_id', table_name=table)
        op.drop_column(table, 'event_id')
