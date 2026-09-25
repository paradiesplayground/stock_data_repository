"""add resumable daily alert production state

Revision ID: 0014_daily_alert_preparation_state_machine
Revises: 0013_daily_alert_preparations
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_daily_alert_preparation_state_machine"
down_revision = "0013_daily_alert_preparations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table, schema = "daily_alert_preparations", "strategy_tracking"
    for name, default in (
        ("stage", "research"), ("status", "pending"),
        ("finalization_status", "pending"), ("validation_status", "pending"),
        ("canonical_run_status", "pending"), ("website_status", "pending"),
        ("email_status", "pending"),
    ):
        op.add_column(table, sa.Column(name, sa.String(32), nullable=False, server_default=default), schema=schema)
    op.add_column(table, sa.Column("last_error", sa.Text()), schema=schema)
    op.add_column(table, sa.Column("website_delivery", postgresql.JSONB()), schema=schema)
    op.add_column(table, sa.Column("email_delivery", postgresql.JSONB()), schema=schema)
    op.create_index("ix_daily_alert_preparations_resume", table, ["status", "stage"], schema=schema)


def downgrade() -> None:
    table, schema = "daily_alert_preparations", "strategy_tracking"
    op.drop_index("ix_daily_alert_preparations_resume", table_name=table, schema=schema)
    for name in ("email_delivery", "website_delivery", "last_error", "email_status", "website_status", "canonical_run_status", "validation_status", "finalization_status", "status", "stage"):
        op.drop_column(table, name, schema=schema)
