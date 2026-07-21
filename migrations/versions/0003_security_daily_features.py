"""Add deterministic daily security features.

Revision ID: 0003_security_daily_features
Revises: 0002_ingestion_checkpoints
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_security_daily_features"
down_revision: str | None = "0002_ingestion_checkpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_daily_features",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "ticker",
            sa.String(32),
            sa.ForeignKey("securities.ticker", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("price_change_12w_pct", sa.Numeric(20, 8)),
        sa.Column("drawdown_52w_pct", sa.Numeric(20, 8)),
        sa.Column("avg_volume_20d", sa.Numeric(24, 4)),
        sa.Column("avg_dollar_volume_20d", sa.Numeric(28, 4)),
        sa.Column("ema_10", sa.Numeric(20, 8)),
        sa.Column("ema_20", sa.Numeric(20, 8)),
        sa.Column("rsi_14", sa.Numeric(20, 8)),
        sa.Column("relative_volume_20d", sa.Numeric(20, 8)),
        sa.Column("revenue_ttm", sa.Numeric(38, 8)),
        sa.Column("revenue_ttm_yoy_pct", sa.Numeric(20, 8)),
        sa.Column("latest_quarter_revenue", sa.Numeric(38, 8)),
        sa.Column("latest_quarter_revenue_yoy_pct", sa.Numeric(20, 8)),
        sa.Column("revenue_concept", sa.String(255)),
        sa.Column("gross_profit_ttm", sa.Numeric(38, 8)),
        sa.Column("gross_margin_ttm_pct", sa.Numeric(20, 8)),
        sa.Column("cash_and_short_term_investments", sa.Numeric(38, 8)),
        sa.Column("total_debt", sa.Numeric(38, 8)),
        sa.Column("current_ratio", sa.Numeric(20, 8)),
        sa.Column("operating_cash_flow_ttm", sa.Numeric(38, 8)),
        sa.Column("capital_expenditures_ttm", sa.Numeric(38, 8)),
        sa.Column("free_cash_flow_ttm", sa.Numeric(38, 8)),
        sa.Column("cash_runway_months", sa.Numeric(20, 4)),
        sa.Column("shares_outstanding", sa.Numeric(38, 8)),
        sa.Column("share_count_yoy_pct", sa.Numeric(20, 8)),
        sa.Column("approximate_market_cap", sa.Numeric(38, 4)),
        sa.Column("latest_financial_period_end", sa.Date()),
        sa.Column("latest_source_filing_date", sa.Date()),
        sa.Column("calculation_version", sa.String(32), nullable=False),
        sa.Column("quality_flags", postgresql.JSONB()),
        sa.Column(
            "calculated_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "ticker", "as_of_date", name="uq_security_features_ticker_date"
        ),
    )
    op.create_index("ix_security_features_date", "security_daily_features", ["as_of_date"])
    op.create_index(
        "ix_security_features_liquidity",
        "security_daily_features",
        ["as_of_date", "avg_dollar_volume_20d"],
    )
    op.create_index(
        "ix_security_features_growth",
        "security_daily_features",
        ["as_of_date", "revenue_ttm_yoy_pct"],
    )


def downgrade() -> None:
    op.drop_index("ix_security_features_growth", table_name="security_daily_features")
    op.drop_index("ix_security_features_liquidity", table_name="security_daily_features")
    op.drop_index("ix_security_features_date", table_name="security_daily_features")
    op.drop_table("security_daily_features")
