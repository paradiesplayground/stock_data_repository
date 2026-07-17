"""Initial authoritative data repository schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "securities",
        sa.Column("ticker", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(512)),
        sa.Column("market", sa.String(32)),
        sa.Column("locale", sa.String(16)),
        sa.Column("currency", sa.String(16)),
        sa.Column("primary_exchange", sa.String(32)),
        sa.Column("security_type", sa.String(32)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cik", sa.String(10)),
        sa.Column("composite_figi", sa.String(32)),
        sa.Column("share_class_figi", sa.String(32)),
        sa.Column("sic_code", sa.String(8)),
        sa.Column("sic_description", sa.String(255)),
        sa.Column("fiscal_year_end", sa.String(4)),
        sa.Column("state_of_incorporation", sa.String(8)),
        sa.Column("last_updated_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_securities_primary_exchange", "securities", ["primary_exchange"])
    op.create_index("ix_securities_security_type", "securities", ["security_type"])
    op.create_index("ix_securities_active", "securities", ["active"])
    op.create_index("ix_securities_cik", "securities", ["cik"])
    op.create_index("ix_securities_sic_code", "securities", ["sic_code"])

    op.create_table(
        "daily_price_bars",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(32), sa.ForeignKey("securities.ticker", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=False),
        sa.Column("high", sa.Numeric(20, 8), nullable=False),
        sa.Column("low", sa.Numeric(20, 8), nullable=False),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(24, 4), nullable=False),
        sa.Column("vwap", sa.Numeric(20, 8)),
        sa.Column("transactions", sa.BigInteger()),
        sa.Column("adjusted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(32), nullable=False, server_default="massive"),
        sa.Column("source_timestamp_ms", sa.BigInteger()),
        sa.Column("ingested_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ticker", "trade_date", name="uq_daily_price_ticker_date"),
    )
    op.create_index("ix_daily_price_date", "daily_price_bars", ["trade_date"])

    op.create_table(
        "financial_facts",
        sa.Column("fact_id", sa.String(64), primary_key=True),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("taxonomy", sa.String(64), nullable=False),
        sa.Column("concept", sa.String(255), nullable=False),
        sa.Column("label", sa.String(512)),
        sa.Column("description", sa.Text()),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(38, 8), nullable=False),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("filed_date", sa.Date()),
        sa.Column("form", sa.String(32)),
        sa.Column("fiscal_year", sa.Integer()),
        sa.Column("fiscal_period", sa.String(16)),
        sa.Column("frame", sa.String(32)),
        sa.Column("accession_number", sa.String(32)),
        sa.Column("source", sa.String(32), nullable=False, server_default="sec-edgar"),
        sa.Column("ingested_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_financial_facts_cik", "financial_facts", ["cik"])
    op.create_index("ix_financial_facts_concept", "financial_facts", ["concept"])
    op.create_index("ix_facts_cik_concept_end", "financial_facts", ["cik", "concept", "period_end"])
    op.create_index("ix_facts_accession", "financial_facts", ["accession_number"])

    op.create_table(
        "filings",
        sa.Column("accession_number", sa.String(32), primary_key=True),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("form", sa.String(32), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("report_date", sa.Date()),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("primary_document", sa.String(512)),
        sa.Column("primary_doc_description", sa.String(1024)),
        sa.Column("file_number", sa.String(128)),
        sa.Column("film_number", sa.String(64)),
        sa.Column("items", sa.Text()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("is_xbrl", sa.Boolean()),
        sa.Column("is_inline_xbrl", sa.Boolean()),
        sa.Column("source_url", sa.Text()),
        sa.Column("ingested_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_filings_cik", "filings", ["cik"])
    op.create_index("ix_filings_form", "filings", ["form"])
    op.create_index("ix_filings_cik_filed", "filings", ["cik", "filed_date"])

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_name", sa.String(128), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True)),
        sa.Column("records_seen", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("records_written", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("source_as_of", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("details", postgresql.JSONB()),
    )
    op.create_index("ix_ingestion_runs_job_name", "ingestion_runs", ["job_name"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])
    op.create_index("ix_ingestion_job_started", "ingestion_runs", ["job_name", "started_at_utc"])


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_table("filings")
    op.drop_table("financial_facts")
    op.drop_table("daily_price_bars")
    op.drop_table("securities")
