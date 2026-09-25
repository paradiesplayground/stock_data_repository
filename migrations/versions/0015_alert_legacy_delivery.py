"""complete legacy daily alert delivery state

Revision ID: 0015_alert_legacy_delivery
Revises: 0014_alert_state
"""

from alembic import op

revision = "0015_alert_legacy_delivery"
down_revision = "0014_alert_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Before 0014, a non-null production_run_id meant the legacy combined
    # persistence/publication/email operation had completed. It had no
    # destination-level receipts, so retries would be unsafe. Mark only the
    # rows that 0014 left in its default/failed state during first startup.
    op.execute("""
        UPDATE strategy_tracking.daily_alert_preparations
        SET stage = 'complete',
            status = 'complete',
            last_error = NULL,
            finalization_status = 'complete',
            validation_status = 'pass',
            canonical_run_status = 'complete',
            website_status = 'complete',
            email_status = 'complete'
        WHERE production_run_id IS NOT NULL
          AND (
            canonical_run_status = 'pending'
            OR website_status = 'failed'
            OR email_status = 'failed'
          )
    """)


def downgrade() -> None:
    # Legacy delivery did not retain independent destination state, so it
    # cannot be reconstructed safely on downgrade.
    pass
