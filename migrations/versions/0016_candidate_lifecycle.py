"""Add persistent candidate lifecycle and anchored trigger state."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_candidate_lifecycle"
down_revision: str | None = "0015_alert_legacy_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_candidate_lifecycles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("strategy_key", sa.String(128), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("first_discovered_date", sa.Date(), nullable=False),
        sa.Column("last_discovery_screen_date", sa.Date()),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("lifecycle_state_since", sa.Date(), nullable=False),
        sa.Column("days_on_watch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discovery_price", sa.Numeric(20, 8)),
        sa.Column("discovery_decline_metric", sa.Numeric(20, 8)),
        sa.Column("active_trigger", sa.Numeric(20, 8)),
        sa.Column("active_trigger_set_date", sa.Date()),
        sa.Column("rolling_trigger", sa.Numeric(20, 8)),
        sa.Column("current_trigger_distance_pct", sa.Numeric(20, 8)),
        sa.Column("best_trigger_distance_pct", sa.Numeric(20, 8)),
        sa.Column("relative_strength_20d", sa.Numeric(20, 8)),
        sa.Column("previous_relative_strength_20d", sa.Numeric(20, 8)),
        sa.Column("last_material_event", sa.String(64)),
        sa.Column("last_material_event_date", sa.Date()),
        sa.Column("archive_reason", sa.Text()),
        sa.Column("archived_date", sa.Date()),
        sa.Column("metadata_json", postgresql.JSONB()),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("strategy_key", "ticker", name="uq_strategy_lifecycle_key_ticker"),
        schema="strategy_tracking",
    )
    op.create_index("ix_strategy_lifecycles_state", "strategy_candidate_lifecycles", ["lifecycle_state"], schema="strategy_tracking")


def downgrade() -> None:
    op.drop_index("ix_strategy_lifecycles_state", table_name="strategy_candidate_lifecycles", schema="strategy_tracking")
    op.drop_table("strategy_candidate_lifecycles", schema="strategy_tracking")
