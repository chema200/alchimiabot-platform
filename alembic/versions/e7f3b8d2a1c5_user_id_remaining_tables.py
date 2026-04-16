"""Add user_id to trade_snapshots, trade_verdicts, change_markers, regime_labels.

Extends multi-tenant isolation to all per-user tables. feature_snapshots and
coin_profiles remain global (system-level data, not per-user).

All existing rows backfilled to user_id=1 (admin).

Revision ID: e7f3b8d2a1c5
Revises: d5e1a7c4f2b9
Create Date: 2026-04-16 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7f3b8d2a1c5'
down_revision: Union[str, Sequence[str], None] = 'd5e1a7c4f2b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('trade_snapshots', 'trade_verdicts', 'change_markers', 'regime_labels'):
        op.add_column(
            table,
            sa.Column('user_id', sa.BigInteger(), nullable=False, server_default='1'),
        )
        op.create_index(f'idx_{table}_user_id', table, ['user_id'])


def downgrade() -> None:
    for table in ('trade_snapshots', 'trade_verdicts', 'change_markers', 'regime_labels'):
        op.drop_index(f'idx_{table}_user_id', table_name=table)
        op.drop_column(table, 'user_id')
