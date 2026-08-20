"""Add canonical decision-contract provenance and technical state.

Revision ID: 0011_decision_contract
Revises: 0010_candidate_decisions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_decision_contract"
down_revision: str | None = "0010_candidate_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "strategy_runs",
        sa.Column("decision_contract_version", sa.String(32)),
        schema="strategy_tracking",
    )
    op.add_column(
        "strategy_candidate_decisions",
        sa.Column("technical_state", sa.String(32)),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_candidate_decisions_technical_state",
        "strategy_candidate_decisions",
        ["technical_state"],
        schema="strategy_tracking",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_strategy_candidate_decisions_technical_state",
        table_name="strategy_candidate_decisions",
        schema="strategy_tracking",
    )
    op.drop_column(
        "strategy_candidate_decisions",
        "technical_state",
        schema="strategy_tracking",
    )
    op.drop_column(
        "strategy_runs",
        "decision_contract_version",
        schema="strategy_tracking",
    )
