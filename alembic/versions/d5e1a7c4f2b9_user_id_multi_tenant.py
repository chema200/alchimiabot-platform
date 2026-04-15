"""Add user_id to trade_outcomes and signal_evaluations for multi-tenant isolation.

All existing rows are backfilled to user_id=1 (admin bridge).
Going forward the bot sends its ADMIN_BRIDGE_USER_ID; when per-user engines
are introduced, each engine will send its own user_id and analytics will
naturally partition by tenant.

Revision ID: d5e1a7c4f2b9
Revises: c1f8a2e5d9b4
Create Date: 2026-04-15 11:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e1a7c4f2b9'
down_revision: Union[str, Sequence[str], None] = 'c1f8a2e5d9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'trade_outcomes',
        sa.Column('user_id', sa.BigInteger(), nullable=False, server_default='1'),
    )
    op.add_column(
        'signal_evaluations',
        sa.Column('user_id', sa.BigInteger(), nullable=False, server_default='1'),
    )
    op.create_index('idx_trade_user_time', 'trade_outcomes', ['user_id', 'entry_time'])
    op.create_index('idx_signal_user_time', 'signal_evaluations', ['user_id', 'timestamp'])


def downgrade() -> None:
    op.drop_index('idx_signal_user_time', table_name='signal_evaluations')
    op.drop_index('idx_trade_user_time', table_name='trade_outcomes')
    op.drop_column('signal_evaluations', 'user_id')
    op.drop_column('trade_outcomes', 'user_id')
