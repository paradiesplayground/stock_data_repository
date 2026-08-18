"""Persist v0.7 strategy candidate decisions.

Revision ID: 0010_candidate_decisions
Revises: 0009_historical_market_sources
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_candidate_decisions"
down_revision: str | None = "0009_historical_market_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_candidate_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("decision_status", sa.String(32), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=False),
        sa.Column("next_condition", sa.Text(), nullable=False),
        sa.Column("current_entry", sa.Numeric(20, 8)),
        sa.Column("pct_above_trigger", sa.Numeric(12, 4)),
        sa.Column("t1_r", sa.Numeric(12, 4)),
        sa.Column("t2_r", sa.Numeric(12, 4)),
        sa.Column("technical_gate_passed", sa.Boolean()),
        sa.Column("market_regime_gate_passed", sa.Boolean()),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["strategy_tracking.strategy_runs.run_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id",
            "ticker",
            name="uq_strategy_candidate_decision_run_ticker",
        ),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_candidate_decisions_run_id",
        "strategy_candidate_decisions",
        ["run_id"],
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_candidate_decisions_decision_status",
        "strategy_candidate_decisions",
        ["decision_status"],
        schema="strategy_tracking",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_strategy_candidate_decisions_decision_status",
        table_name="strategy_candidate_decisions",
        schema="strategy_tracking",
    )
    op.drop_index(
        "ix_strategy_candidate_decisions_run_id",
        table_name="strategy_candidate_decisions",
        schema="strategy_tracking",
    )
    op.drop_table("strategy_candidate_decisions", schema="strategy_tracking")
