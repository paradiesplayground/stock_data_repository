"""Add append-only candidate lifecycle transition observations."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_lifecycle_observations"
down_revision: str | None = "0016_candidate_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_candidate_lifecycle_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("strategy_key", sa.String(128), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("from_state", sa.String(32)),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("outcome_status", sa.String(32), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("source_run_id", sa.String(36)),
        sa.Column("observed_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "strategy_key", "ticker", "observation_date", "event",
            name="uq_strategy_lifecycle_observation",
        ),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_lifecycle_observations_ticker_date",
        "strategy_candidate_lifecycle_observations",
        ["ticker", "observation_date"],
        schema="strategy_tracking",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_strategy_lifecycle_observations_ticker_date",
        table_name="strategy_candidate_lifecycle_observations",
        schema="strategy_tracking",
    )
    op.drop_table("strategy_candidate_lifecycle_observations", schema="strategy_tracking")
