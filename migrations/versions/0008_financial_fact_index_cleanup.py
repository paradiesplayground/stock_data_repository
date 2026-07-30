"""Remove redundant financial-fact indexes.

Revision ID: 0008_financial_fact_index_cleanup
Revises: 0007_strategy_run_report
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_financial_fact_index_cleanup"
down_revision: str | None = "0007_strategy_run_report"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_financial_facts_cik", table_name="financial_facts")
    op.drop_index("ix_financial_facts_concept", table_name="financial_facts")


def downgrade() -> None:
    op.create_index(
        "ix_financial_facts_cik",
        "financial_facts",
        ["cik"],
    )
    op.create_index(
        "ix_financial_facts_concept",
        "financial_facts",
        ["concept"],
    )
