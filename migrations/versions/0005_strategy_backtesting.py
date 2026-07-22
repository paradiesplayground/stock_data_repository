"""Add deterministic strategy simulation storage.

Revision ID: 0005_strategy_backtesting
Revises: 0004_history_strategy
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_strategy_backtesting"
down_revision: str | None = "0004_history_strategy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_simulation_runs",
        sa.Column("simulation_id", sa.String(36), primary_key=True),
        sa.Column(
            "strategy_definition_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "strategy_tracking.strategy_definitions.id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("scenario_key", sa.String(255), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("feature_calculation_version", sa.String(32), nullable=False),
        sa.Column("source_runs_hash", sa.String(64), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column(
            "generated_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("scenario_key", name="uq_strategy_simulation_scenario"),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_simulations_definition_dates",
        "strategy_simulation_runs",
        ["strategy_definition_id", "start_date", "end_date"],
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_simulation_runs_status",
        "strategy_simulation_runs",
        ["status"],
        schema="strategy_tracking",
    )

    op.create_table(
        "strategy_simulation_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "simulation_id",
            sa.String(36),
            sa.ForeignKey(
                "strategy_tracking.strategy_simulation_runs.simulation_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("source_run_id", sa.String(36), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("order_expiration_date", sa.Date()),
        sa.Column("entry_date", sa.Date()),
        sa.Column("exit_date", sa.Date()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("initial_shares", sa.BigInteger()),
        sa.Column("remaining_shares", sa.BigInteger()),
        sa.Column("entry_price", sa.Numeric(20, 8)),
        sa.Column("initial_stop_price", sa.Numeric(20, 8)),
        sa.Column("target_one_price", sa.Numeric(20, 8)),
        sa.Column("target_two_price", sa.Numeric(20, 8)),
        sa.Column("exit_price", sa.Numeric(20, 8)),
        sa.Column("planned_risk", sa.Numeric(20, 4)),
        sa.Column("net_pnl", sa.Numeric(20, 4)),
        sa.Column("realized_r", sa.Numeric(20, 8)),
        sa.Column("holding_sessions", sa.Integer()),
        sa.Column("exit_reason", sa.String(64)),
        sa.Column("details", postgresql.JSONB()),
        sa.UniqueConstraint(
            "simulation_id",
            "source_run_id",
            "ticker",
            name="uq_strategy_simulation_trade_signal",
        ),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_simulation_trades_ticker",
        "strategy_simulation_trades",
        ["ticker"],
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_simulation_trades_status",
        "strategy_simulation_trades",
        ["status"],
        schema="strategy_tracking",
    )

    op.create_table(
        "strategy_simulation_equity",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "simulation_id",
            sa.String(36),
            sa.ForeignKey(
                "strategy_tracking.strategy_simulation_runs.simulation_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("cash", sa.Numeric(20, 4), nullable=False),
        sa.Column("equity", sa.Numeric(20, 4), nullable=False),
        sa.Column("drawdown_pct", sa.Numeric(20, 8), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("planned_open_risk", sa.Numeric(20, 4), nullable=False),
        sa.UniqueConstraint(
            "simulation_id",
            "market_date",
            name="uq_strategy_simulation_equity_date",
        ),
        schema="strategy_tracking",
    )


def downgrade() -> None:
    op.drop_table("strategy_simulation_equity", schema="strategy_tracking")
    op.drop_index(
        "ix_strategy_simulation_trades_status",
        table_name="strategy_simulation_trades",
        schema="strategy_tracking",
    )
    op.drop_index(
        "ix_strategy_simulation_trades_ticker",
        table_name="strategy_simulation_trades",
        schema="strategy_tracking",
    )
    op.drop_table("strategy_simulation_trades", schema="strategy_tracking")
    op.drop_index(
        "ix_strategy_simulation_runs_status",
        table_name="strategy_simulation_runs",
        schema="strategy_tracking",
    )
    op.drop_index(
        "ix_strategy_simulations_definition_dates",
        table_name="strategy_simulation_runs",
        schema="strategy_tracking",
    )
    op.drop_table("strategy_simulation_runs", schema="strategy_tracking")
