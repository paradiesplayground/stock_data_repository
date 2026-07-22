"""Add reusable close-to-close daily return feature.

Revision ID: 0006_daily_return_feature
Revises: 0005_strategy_backtesting
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_daily_return_feature"
down_revision: str | None = "0005_strategy_backtesting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "security_daily_features",
        sa.Column("daily_return_pct", sa.Numeric(20, 8)),
    )


def downgrade() -> None:
    op.drop_column("security_daily_features", "daily_return_pct")
