"""strategy attribution on trade_outcomes

Revision ID: a1f2c3d4e5b6
Revises: f8c2d9a4b5e6
Create Date: 2026-04-26 14:30:00.000000

Adds strategy_id, strategy_name, strategy_template columns so labs can
roll up trade performance per strategy. The bot already persists these
in its own hl_trading_history; the platform mirror was missing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1f2c3d4e5b6'
down_revision: Union[str, Sequence[str], None] = 'f8c2d9a4b5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('trade_outcomes',
                  sa.Column('strategy_id', sa.BigInteger(), nullable=True))
    op.add_column('trade_outcomes',
                  sa.Column('strategy_name', sa.String(length=60), nullable=True))
    op.add_column('trade_outcomes',
                  sa.Column('strategy_template', sa.String(length=20), nullable=True))
    op.create_index('idx_trade_strategy_id',
                    'trade_outcomes', ['user_id', 'strategy_id'])
    op.create_index('idx_trade_strategy_template',
                    'trade_outcomes', ['user_id', 'strategy_template'])


def downgrade() -> None:
    op.drop_index('idx_trade_strategy_template', table_name='trade_outcomes')
    op.drop_index('idx_trade_strategy_id', table_name='trade_outcomes')
    op.drop_column('trade_outcomes', 'strategy_template')
    op.drop_column('trade_outcomes', 'strategy_name')
    op.drop_column('trade_outcomes', 'strategy_id')
