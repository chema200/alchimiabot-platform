"""change markers

Revision ID: c1f8a2e5d9b4
Revises: ba6c7600bba1
Create Date: 2026-04-08 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c1f8a2e5d9b4'
down_revision: Union[str, Sequence[str], None] = 'ba6c7600bba1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create change_markers table."""
    op.create_table(
        'change_markers',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('category', sa.String(30), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('coin', sa.String(20)),
        sa.Column('side', sa.String(10)),
        sa.Column('mode', sa.String(20)),
        sa.Column('parameter', sa.String(50)),
        sa.Column('old_value', sa.Float()),
        sa.Column('new_value', sa.Float()),
        sa.Column('batch_id', sa.String(50)),
        sa.Column('batch_label', sa.String(200)),
        sa.Column('config_snapshot', postgresql.JSONB(astext_type=sa.Text())),
        sa.Column('impact_status', sa.String(20), server_default='PENDING'),
        sa.Column('impact_data', postgresql.JSONB(astext_type=sa.Text())),
        sa.Column('impact_calculated_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_markers_timestamp', 'change_markers', [sa.text('timestamp DESC')])
    op.create_index('idx_markers_category', 'change_markers', ['category'])
    op.create_index('idx_markers_coin_side_mode', 'change_markers', ['coin', 'side', 'mode'])
    op.create_index('idx_markers_batch', 'change_markers', ['batch_id'])


def downgrade() -> None:
    """Drop change_markers table."""
    op.drop_index('idx_markers_batch', table_name='change_markers')
    op.drop_index('idx_markers_coin_side_mode', table_name='change_markers')
    op.drop_index('idx_markers_category', table_name='change_markers')
    op.drop_index('idx_markers_timestamp', table_name='change_markers')
    op.drop_table('change_markers')
