"""trade snapshots and verdicts

Revision ID: ba6c7600bba1
Revises: 5fd4bf08acfe
Create Date: 2026-04-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba6c7600bba1'
down_revision: Union[str, Sequence[str], None] = '5fd4bf08acfe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create trade_snapshots and trade_verdicts tables."""
    # ── trade_snapshots ──
    op.create_table(
        'trade_snapshots',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('trade_id', sa.String(100), nullable=False),
        sa.Column('coin', sa.String(20), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('mid_price', sa.Float()),
        sa.Column('sl_price', sa.Float()),
        sa.Column('tp_price', sa.Float()),
        sa.Column('high_water_mark', sa.Float()),
        sa.Column('entry_price', sa.Float()),
        sa.Column('gross_pnl', sa.Float()),
        sa.Column('pnl_pct', sa.Float()),
        sa.Column('hold_seconds', sa.Integer()),
        sa.Column('partial_closed', sa.Boolean(), server_default='false'),
        sa.Column('mfe_pct', sa.Float(), server_default='0'),
        sa.Column('mae_pct', sa.Float(), server_default='0'),
    )
    op.create_index('idx_trade_snapshots_trade_id', 'trade_snapshots', ['trade_id'])
    op.create_index('idx_trade_snapshots_timestamp', 'trade_snapshots', ['timestamp'])

    # ── trade_verdicts ──
    op.create_table(
        'trade_verdicts',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('trade_outcome_id', sa.BigInteger(), sa.ForeignKey('trade_outcomes.id')),
        sa.Column('coin', sa.String(20), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('mode', sa.String(20)),
        sa.Column('entry_time', sa.DateTime(timezone=True)),
        sa.Column('exit_time', sa.DateTime(timezone=True)),
        # Summary
        sa.Column('verdict', sa.String(20), nullable=False),
        sa.Column('verdict_reason', sa.Text()),
        # Entry analysis
        sa.Column('entry_score', sa.Float()),
        sa.Column('entry_quality', sa.String(10)),
        sa.Column('entry_timing', sa.String(50)),
        sa.Column('trend_aligned', sa.Boolean()),
        # Execution analysis
        sa.Column('mfe_pct', sa.Float()),
        sa.Column('mae_pct', sa.Float()),
        sa.Column('mfe_capture_pct', sa.Float()),
        sa.Column('time_in_profit_pct', sa.Float()),
        sa.Column('sl_moved', sa.Boolean(), server_default='false'),
        sa.Column('sl_moves_count', sa.Integer(), server_default='0'),
        sa.Column('max_sl_distance_pct', sa.Float()),
        sa.Column('min_sl_distance_pct', sa.Float()),
        # Result
        sa.Column('gross_pnl', sa.Float()),
        sa.Column('fee', sa.Float()),
        sa.Column('net_pnl', sa.Float()),
        sa.Column('fee_killed', sa.Boolean(), server_default='false'),
        sa.Column('exit_reason', sa.String(50)),
        # What could improve
        sa.Column('improvements', sa.JSON()),
        sa.Column('counterfactual', sa.JSON()),
        # Meta
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_trade_verdicts_trade_outcome_id', 'trade_verdicts', ['trade_outcome_id'])
    op.create_index('idx_trade_verdicts_coin', 'trade_verdicts', ['coin'])


def downgrade() -> None:
    """Drop trade_verdicts and trade_snapshots tables."""
    op.drop_index('idx_trade_verdicts_coin', table_name='trade_verdicts')
    op.drop_index('idx_trade_verdicts_trade_outcome_id', table_name='trade_verdicts')
    op.drop_table('trade_verdicts')
    op.drop_index('idx_trade_snapshots_timestamp', table_name='trade_snapshots')
    op.drop_index('idx_trade_snapshots_trade_id', table_name='trade_snapshots')
    op.drop_table('trade_snapshots')
