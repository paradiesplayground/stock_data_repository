"""Add prior-session 20-day levels and ATR to derived features (calculation 1.6.0)."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0018_prior_session_features"
down_revision: str | None = "0017_lifecycle_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("prior_high_20d", "prior_low_20d", "prior_atr_14")


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(
            "security_daily_features",
            sa.Column(column, sa.Numeric(20, 8), nullable=True),
        )


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("security_daily_features", column)
