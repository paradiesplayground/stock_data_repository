"""Add durable ingestion checkpoints.

Revision ID: 0002_ingestion_checkpoints
Revises: 0001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_ingestion_checkpoints"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_checkpoints",
        sa.Column("job_name", sa.String(128), primary_key=True),
        sa.Column("checkpoint_date", sa.Date(), nullable=False),
        sa.Column("details", postgresql.JSONB()),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ingestion_checkpoints")
