"""Store typed research findings with each daily-alert research checkpoint."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_research_fields"
down_revision: str | None = "0018_prior_session_features"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_alert_preparation_research",
        sa.Column("research_fields", postgresql.JSONB(), nullable=True),
        schema="strategy_tracking",
    )


def downgrade() -> None:
    op.drop_column("daily_alert_preparation_research", "research_fields", schema="strategy_tracking")
