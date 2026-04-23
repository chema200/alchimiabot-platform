"""Drop unused ML/experiment tables.

As of 2026-04-23 these five tables have 0 rows and no readers/writers in
the codebase. They were scaffolding for an ML pipeline that never
landed. Keeping them live alongside vacant SQLAlchemy models confuses
future contributors and the Research Lab UI.

Dropped:
  - experiment_runs (ExperimentRun model)
  - replay_runs     (ReplayRun model)
  - dataset_registry (DatasetRecord model)
  - model_registry   (ModelRecord model)
  - coin_profiles    (CoinProfile model — platform side; the bot DB still
                      has its own hl_coin_profiles table which is alive)

Reversible via downgrade().

Revision ID: f8c2d9a4b5e6
Revises: e7f3b8d2a1c5
Create Date: 2026-04-23 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f8c2d9a4b5e6"
down_revision = "e7f3b8d2a1c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS so re-running is safe if someone already cleaned manually.
    op.execute("DROP TABLE IF EXISTS experiment_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS replay_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS model_registry CASCADE")
    op.execute("DROP TABLE IF EXISTS dataset_registry CASCADE")
    op.execute("DROP TABLE IF EXISTS coin_profiles CASCADE")


def downgrade() -> None:
    # Minimum shape to restore, matching the original SQLAlchemy models.
    # Recreates empty tables so imports don't crash; existing audit/ML
    # code that may resurrect these would then populate them.

    op.create_table(
        "coin_profiles",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("coin", sa.String(20), nullable=False, index=True),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("trades", sa.Integer, default=0),
        sa.Column("wins", sa.Integer, default=0),
        sa.Column("losses", sa.Integer, default=0),
        sa.Column("total_pnl", sa.Float, default=0),
        sa.Column("avg_pnl", sa.Float, default=0),
        sa.Column("win_rate", sa.Float, default=0),
        sa.Column("avg_hold_sec", sa.Integer, default=0),
        sa.Column("best_regime", sa.String(30)),
        sa.Column("worst_regime", sa.String(30)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "dataset_registry",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("row_count", sa.Integer),
        sa.Column("feature_version", sa.String(100)),
        sa.Column("label_type", sa.String(50)),
        sa.Column("date_from", sa.DateTime(timezone=True)),
        sa.Column("date_to", sa.DateTime(timezone=True)),
        sa.Column("coins", sa.JSON),
        sa.Column("params", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "model_registry",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("model_type", sa.String(50)),
        sa.Column("dataset_id", sa.BigInteger),
        sa.Column("path", sa.String(500)),
        sa.Column("feature_version", sa.String(100)),
        sa.Column("metrics", sa.JSON),
        sa.Column("status", sa.String(20), default="trained"),
        sa.Column("promoted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text),
    )
    op.create_index("idx_model_name_version", "model_registry", ["name", "version"])

    op.create_table(
        "replay_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200)),
        sa.Column("run_type", sa.String(30)),
        sa.Column("date_from", sa.DateTime(timezone=True)),
        sa.Column("date_to", sa.DateTime(timezone=True)),
        sa.Column("coins", sa.JSON),
        sa.Column("params", sa.JSON),
        sa.Column("total_trades", sa.Integer),
        sa.Column("net_pnl", sa.Float),
        sa.Column("win_rate", sa.Float),
        sa.Column("sharpe", sa.Float),
        sa.Column("max_drawdown", sa.Float),
        sa.Column("results", sa.JSON),
        sa.Column("events_processed", sa.Integer),
        sa.Column("elapsed_sec", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("hypothesis", sa.Text),
        sa.Column("params", sa.JSON),
        sa.Column("baseline_params", sa.JSON),
        sa.Column("status", sa.String(20), default="created"),
        sa.Column("results", sa.JSON),
        sa.Column("baseline_results", sa.JSON),
        sa.Column("promoted", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text),
    )
