"""add durable daily alert preparation checkpoints

Revision ID: 0013_daily_alert_preparations
Revises: 0012_buyability_contract_v08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_daily_alert_preparations"
down_revision = "0012_buyability_contract_v08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = "strategy_tracking"
    op.create_table(
        "daily_alert_preparations",
        sa.Column("preparation_id", sa.String(36), primary_key=True),
        sa.Column("preparation_key", sa.String(255), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("strategy_key", sa.String(128), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("final_payload", postgresql.JSONB()),
        sa.Column("validation", postgresql.JSONB()),
        sa.Column("production_run_id", sa.String(36)),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("preparation_key", name="uq_daily_alert_preparation_key"),
        schema=schema,
    )
    op.create_index("ix_daily_alert_preparations_date", "daily_alert_preparations", ["as_of_date"], schema=schema)
    op.create_table(
        "daily_alert_preparation_research",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("preparation_id", sa.String(36), sa.ForeignKey(f"{schema}.daily_alert_preparations.preparation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("required_dimensions", postgresql.JSONB(), nullable=False),
        sa.Column("qualitative_blockers", postgresql.JSONB(), nullable=False),
        sa.Column("qualitative_flags", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_decision", postgresql.JSONB()),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("preparation_id", "ticker", name="uq_daily_alert_preparation_research"),
        schema=schema,
    )
    op.create_index("ix_daily_alert_preparation_research_preparation", "daily_alert_preparation_research", ["preparation_id"], schema=schema)


def downgrade() -> None:
    schema = "strategy_tracking"
    op.drop_table("daily_alert_preparation_research", schema=schema)
    op.drop_table("daily_alert_preparations", schema=schema)
