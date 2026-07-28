"""Store the completed human-readable strategy report.

Revision ID: 0007_strategy_run_report
Revises: 0006_daily_return_feature
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_strategy_run_report"
down_revision: str | None = "0006_daily_return_feature"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "strategy_runs",
        sa.Column("report_markdown", sa.Text()),
        schema="strategy_tracking",
    )


def downgrade() -> None:
    op.drop_column(
        "strategy_runs",
        "report_markdown",
        schema="strategy_tracking",
    )
