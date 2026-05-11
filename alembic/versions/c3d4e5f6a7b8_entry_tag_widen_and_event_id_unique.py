"""entry_tag widen to 100 + full UNIQUE on event_id for ON CONFLICT

Revision ID: c3d4e5f6a7b8
Revises: b2e3d4f5a6c7
Create Date: 2026-05-11 14:15:00.000000

Dos arreglos relacionados con el outbox del bot (V93+ tras invert_signal).

1) entry_tag varchar(50) -> varchar(100)
   El bot ahora puede emitir tags con sufijo "+inverted" (V93). Tags reales
   observados ya rompen el límite:
     "trend_15m+macro+rsi+strategy:Patient loose LONG+inverted"  (56 chars)
   El bot DB lo guarda en varchar(100), platform DB no. Resultado:
   StringDataRightTruncationError en cada INSERT a trade_outcomes para
   trades invertidos. 88 eventos atrapados en outbox.

2) event_id UNIQUE full (no partial) en las 4 tablas ingest
   La migracion previa b2e3d4f5a6c7 creo indices unique partial
   (WHERE event_id IS NOT NULL). Postgres rechaza ON CONFLICT contra
   indices partial -> InvalidColumnReferenceError.
   Solucion: reemplazar el indice partial por un UNIQUE CONSTRAINT full.
   No filas null pueden colisionar entre si en un UNIQUE constraint
   (multiples NULLs estan permitidos por SQL standard), por lo que el
   comportamiento para filas legacy se mantiene equivalente.

Tablas afectadas: trade_outcomes, signal_evaluations, change_markers,
regime_labels.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2e3d4f5a6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EVENT_ID_TABLES = [
    "trade_outcomes",
    "signal_evaluations",
    "change_markers",
    "regime_labels",
]


def upgrade() -> None:
    op.alter_column(
        "trade_outcomes",
        "entry_tag",
        existing_type=sa.String(length=50),
        type_=sa.String(length=100),
        existing_nullable=True,
    )

    for table in EVENT_ID_TABLES:
        op.drop_index(f"uq_{table}_event_id", table_name=table)
        op.create_unique_constraint(
            f"uq_{table}_event_id",
            table,
            ["event_id"],
        )


def downgrade() -> None:
    for table in EVENT_ID_TABLES:
        op.drop_constraint(f"uq_{table}_event_id", table, type_="unique")
        op.create_index(
            f"uq_{table}_event_id",
            table,
            ["event_id"],
            unique=True,
            postgresql_where=sa.text("event_id IS NOT NULL"),
        )

    op.alter_column(
        "trade_outcomes",
        "entry_tag",
        existing_type=sa.String(length=100),
        type_=sa.String(length=50),
        existing_nullable=True,
    )
