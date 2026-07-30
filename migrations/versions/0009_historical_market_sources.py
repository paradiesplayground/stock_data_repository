"""Add survivorship-safe historical market source data.

Revision ID: 0009_historical_market_sources
Revises: 0008_fact_index_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_historical_market_sources"
down_revision: str | None = "0008_fact_index_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("securities", sa.Column("list_date", sa.Date()))
    op.add_column("securities", sa.Column("delisted_date", sa.Date()))

    op.create_table(
        "security_reference_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="massive",
        ),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "ingested_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "ticker",
            "as_of_date",
            "source",
            name="uq_security_reference_snapshot",
        ),
    )
    op.create_index(
        "ix_security_reference_snapshot_date_ticker",
        "security_reference_snapshots",
        ["as_of_date", "ticker"],
    )

    op.create_table(
        "raw_daily_price_bars",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=False),
        sa.Column("high", sa.Numeric(20, 8), nullable=False),
        sa.Column("low", sa.Numeric(20, 8), nullable=False),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(24, 4), nullable=False),
        sa.Column("vwap", sa.Numeric(20, 8)),
        sa.Column("transactions", sa.BigInteger()),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="massive",
        ),
        sa.Column("source_timestamp_ms", sa.BigInteger()),
        sa.Column(
            "ingested_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "ticker",
            "trade_date",
            name="uq_raw_daily_price_ticker_date",
        ),
    )
    op.create_index(
        "ix_raw_daily_price_date",
        "raw_daily_price_bars",
        ["trade_date"],
    )

    op.create_table(
        "stock_splits",
        sa.Column("provider_id", sa.String(128), primary_key=True),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("execution_date", sa.Date(), nullable=False),
        sa.Column("split_from", sa.Numeric(28, 10)),
        sa.Column("split_to", sa.Numeric(28, 10)),
        sa.Column("adjustment_type", sa.String(32)),
        sa.Column("historical_adjustment_factor", sa.Numeric(28, 14)),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="massive",
        ),
        sa.Column(
            "ingested_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_stock_splits_ticker_date",
        "stock_splits",
        ["ticker", "execution_date"],
    )

    op.create_table(
        "cash_dividends",
        sa.Column("provider_id", sa.String(128), primary_key=True),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("ex_dividend_date", sa.Date(), nullable=False),
        sa.Column("cash_amount", sa.Numeric(28, 10)),
        sa.Column("split_adjusted_cash_amount", sa.Numeric(28, 10)),
        sa.Column("currency", sa.String(16)),
        sa.Column("declaration_date", sa.Date()),
        sa.Column("record_date", sa.Date()),
        sa.Column("pay_date", sa.Date()),
        sa.Column("frequency", sa.Integer()),
        sa.Column("distribution_type", sa.String(32)),
        sa.Column("historical_adjustment_factor", sa.Numeric(28, 14)),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="massive",
        ),
        sa.Column(
            "ingested_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_cash_dividends_ticker_date",
        "cash_dividends",
        ["ticker", "ex_dividend_date"],
    )

    op.create_table(
        "ticker_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("identifier", sa.String(64), nullable=False),
        sa.Column("entity_name", sa.String(512)),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(32)),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="massive",
        ),
        sa.Column(
            "ingested_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ticker_events_ticker_date",
        "ticker_events",
        ["ticker", "event_date"],
    )
    op.create_index(
        "ix_ticker_events_identifier",
        "ticker_events",
        ["identifier"],
    )


def downgrade() -> None:
    op.drop_index("ix_ticker_events_identifier", table_name="ticker_events")
    op.drop_index("ix_ticker_events_ticker_date", table_name="ticker_events")
    op.drop_table("ticker_events")
    op.drop_index(
        "ix_cash_dividends_ticker_date",
        table_name="cash_dividends",
    )
    op.drop_table("cash_dividends")
    op.drop_index("ix_stock_splits_ticker_date", table_name="stock_splits")
    op.drop_table("stock_splits")
    op.drop_index("ix_raw_daily_price_date", table_name="raw_daily_price_bars")
    op.drop_table("raw_daily_price_bars")
    op.drop_index(
        "ix_security_reference_snapshot_date_ticker",
        table_name="security_reference_snapshots",
    )
    op.drop_table("security_reference_snapshots")
    op.drop_column("securities", "delisted_date")
    op.drop_column("securities", "list_date")
