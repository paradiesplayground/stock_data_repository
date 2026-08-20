"""add v0.8 buyability decision fields

Revision ID: 0012_buyability_contract_v08
Revises: 0011_decision_contract
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_buyability_contract_v08"
down_revision = "0011_decision_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "strategy_candidate_decisions"
    schema = "strategy_tracking"
    op.add_column(table, sa.Column("buyability_status", sa.String(32)), schema=schema)
    op.add_column(table, sa.Column("buy_conditions", postgresql.JSONB()), schema=schema)
    op.add_column(table, sa.Column("remaining_gate_count", sa.Integer()), schema=schema)
    op.add_column(table, sa.Column("current_price", sa.Numeric(20, 8)), schema=schema)
    op.add_column(table, sa.Column("trigger_price", sa.Numeric(20, 8)), schema=schema)
    op.add_column(table, sa.Column("distance_to_trigger_pct", sa.Numeric(12, 4)), schema=schema)
    op.add_column(table, sa.Column("invalidation_price", sa.Numeric(20, 8)), schema=schema)
    op.create_index("ix_strategy_candidate_decisions_buyability_status", table, ["buyability_status"], schema=schema)


def downgrade() -> None:
    table = "strategy_candidate_decisions"
    schema = "strategy_tracking"
    op.drop_index("ix_strategy_candidate_decisions_buyability_status", table_name=table, schema=schema)
    for column in ("invalidation_price", "distance_to_trigger_pct", "trigger_price", "current_price", "remaining_gate_count", "buy_conditions", "buyability_status"):
        op.drop_column(table, column, schema=schema)
