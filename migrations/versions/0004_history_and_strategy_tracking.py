"""Add point-in-time history, versioned features, and strategy tracking.

Revision ID: 0004_history_strategy
Revises: 0003_security_daily_features
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_history_strategy"
down_revision: str | None = "0003_security_daily_features"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "financial_facts",
        sa.Column("available_at_utc", sa.DateTime(timezone=True)),
    )
    op.execute(
        """
        UPDATE financial_facts AS fact
        SET available_at_utc = filing.accepted_at
        FROM filings AS filing
        WHERE filing.accession_number = fact.accession_number
          AND filing.accepted_at IS NOT NULL
        """
    )

    op.create_table(
        "security_reference_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "observed_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "ticker", "source", "record_hash", name="uq_security_history_record"
        ),
    )
    op.create_index(
        "ix_security_history_ticker_observed",
        "security_reference_history",
        ["ticker", "observed_at_utc"],
    )
    op.execute(
        """
        INSERT INTO security_reference_history
            (ticker, source, record_hash, snapshot, observed_at_utc)
        SELECT
            ticker,
            'migration-bootstrap',
            md5(concat_ws('|', ticker, name, market, locale, currency,
                primary_exchange, security_type, active, cik, composite_figi,
                share_class_figi, sic_code, sic_description, fiscal_year_end,
                state_of_incorporation)),
            jsonb_strip_nulls(jsonb_build_object(
                'ticker', ticker,
                'name', name,
                'market', market,
                'locale', locale,
                'currency', currency,
                'primary_exchange', primary_exchange,
                'security_type', security_type,
                'active', active,
                'cik', cik,
                'composite_figi', composite_figi,
                'share_class_figi', share_class_figi,
                'sic_code', sic_code,
                'sic_description', sic_description,
                'fiscal_year_end', fiscal_year_end,
                'state_of_incorporation', state_of_incorporation
            )),
            COALESCE(last_updated_utc, now())
        FROM securities
        """
    )

    op.create_table(
        "daily_price_bar_revisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=False),
        sa.Column("high", sa.Numeric(20, 8), nullable=False),
        sa.Column("low", sa.Numeric(20, 8), nullable=False),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(24, 4), nullable=False),
        sa.Column("vwap", sa.Numeric(20, 8)),
        sa.Column("transactions", sa.BigInteger()),
        sa.Column("adjusted", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_timestamp_ms", sa.BigInteger()),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column(
            "observed_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "ticker",
            "trade_date",
            "source",
            "record_hash",
            name="uq_daily_price_revision_record",
        ),
    )
    op.create_index(
        "ix_daily_price_revision_ticker_date",
        "daily_price_bar_revisions",
        ["ticker", "trade_date"],
    )

    op.drop_constraint(
        "uq_security_features_ticker_date",
        "security_daily_features",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_security_features_ticker_date_version",
        "security_daily_features",
        ["ticker", "as_of_date", "calculation_version"],
    )
    for name, column_type in (
        ("reference_name", sa.String(512)),
        ("reference_primary_exchange", sa.String(32)),
        ("reference_security_type", sa.String(32)),
        ("reference_active", sa.Boolean()),
        ("reference_sic_code", sa.String(8)),
        ("reference_sic_description", sa.String(255)),
    ):
        op.add_column("security_daily_features", sa.Column(name, column_type))
    op.execute(
        """
        UPDATE security_daily_features AS feature
        SET reference_name = security.name,
            reference_primary_exchange = security.primary_exchange,
            reference_security_type = security.security_type,
            reference_active = security.active,
            reference_sic_code = security.sic_code,
            reference_sic_description = security.sic_description
        FROM securities AS security
        WHERE security.ticker = feature.ticker
        """
    )
    for name, column_type in (
        ("price_change_20d_pct", sa.Numeric(20, 8)),
        ("drawdown_12w_high_pct", sa.Numeric(20, 8)),
        ("high_20d", sa.Numeric(20, 8)),
        ("low_20d", sa.Numeric(20, 8)),
        ("high_60d", sa.Numeric(20, 8)),
        ("low_60d", sa.Numeric(20, 8)),
        ("distance_to_20d_high_pct", sa.Numeric(20, 8)),
        ("distance_to_60d_high_pct", sa.Numeric(20, 8)),
        ("atr_14", sa.Numeric(20, 8)),
        ("atr_14_pct", sa.Numeric(20, 8)),
        ("overnight_gap_pct", sa.Numeric(20, 8)),
        ("relative_return_20d_vs_qqq_pct", sa.Numeric(20, 8)),
    ):
        op.add_column("security_daily_features", sa.Column(name, column_type))
    op.add_column(
        "security_daily_features",
        sa.Column("source_data_cutoff_utc", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "security_daily_features",
        sa.Column("source_manifest", postgresql.JSONB()),
    )

    op.execute("CREATE SCHEMA IF NOT EXISTS strategy_tracking")

    op.create_table(
        "strategy_definitions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("strategy_key", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("skill_fingerprint", sa.String(128)),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("strategy_key", "version", name="uq_strategy_key_version"),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_definitions_strategy_key",
        "strategy_definitions",
        ["strategy_key"],
        schema="strategy_tracking",
    )

    op.create_table(
        "strategy_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column(
            "strategy_definition_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "strategy_tracking.strategy_definitions.id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("feature_calculation_version", sa.String(32)),
        sa.Column("data_cutoff_at_utc", sa.DateTime(timezone=True)),
        sa.Column("filters", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "generated_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_strategy_run_idempotency"),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_runs_run_type",
        "strategy_runs",
        ["run_type"],
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_runs_definition_date",
        "strategy_runs",
        ["strategy_definition_id", "as_of_date"],
        schema="strategy_tracking",
    )

    op.create_table(
        "strategy_candidates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("strategy_tracking.strategy_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32)),
        sa.Column("score", sa.Numeric(10, 4)),
        sa.Column("score_components", postgresql.JSONB()),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("reasons", postgresql.JSONB()),
        sa.Column("trade_plan", postgresql.JSONB()),
        sa.Column("payload", postgresql.JSONB()),
        sa.UniqueConstraint(
            "run_id", "ticker", name="uq_strategy_candidate_run_ticker"
        ),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_candidates_ticker",
        "strategy_candidates",
        ["ticker"],
        schema="strategy_tracking",
    )

    op.create_table(
        "strategy_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("strategy_tracking.strategy_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(32)),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("accession_number", sa.String(32)),
        sa.Column("published_at_utc", sa.DateTime(timezone=True)),
        sa.Column("accepted_at_utc", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at_utc", sa.DateTime(timezone=True)),
        sa.Column("summary", sa.Text()),
        sa.Column("details", postgresql.JSONB()),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_evidence_run_ticker",
        "strategy_evidence",
        ["run_id", "ticker"],
        schema="strategy_tracking",
    )

    op.create_table(
        "strategy_outcome_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("strategy_tracking.strategy_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("horizon", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32)),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("execution_assumptions", postgresql.JSONB()),
        sa.Column(
            "observed_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id",
            "ticker",
            "observation_date",
            "horizon",
            name="uq_strategy_outcome_observation",
        ),
        schema="strategy_tracking",
    )
    op.create_index(
        "ix_strategy_outcomes_ticker_date",
        "strategy_outcome_observations",
        ["ticker", "observation_date"],
        schema="strategy_tracking",
    )


def downgrade() -> None:
    op.drop_table("strategy_outcome_observations", schema="strategy_tracking")
    op.drop_table("strategy_evidence", schema="strategy_tracking")
    op.drop_table("strategy_candidates", schema="strategy_tracking")
    op.drop_table("strategy_runs", schema="strategy_tracking")
    op.drop_table("strategy_definitions", schema="strategy_tracking")
    op.execute("DROP SCHEMA strategy_tracking")

    op.drop_column("security_daily_features", "source_manifest")
    op.drop_column("security_daily_features", "source_data_cutoff_utc")
    for name in (
        "relative_return_20d_vs_qqq_pct",
        "overnight_gap_pct",
        "atr_14_pct",
        "atr_14",
        "distance_to_60d_high_pct",
        "distance_to_20d_high_pct",
        "low_60d",
        "high_60d",
        "low_20d",
        "high_20d",
        "drawdown_12w_high_pct",
        "price_change_20d_pct",
        "reference_sic_description",
        "reference_sic_code",
        "reference_active",
        "reference_security_type",
        "reference_primary_exchange",
        "reference_name",
    ):
        op.drop_column("security_daily_features", name)
    op.drop_constraint(
        "uq_security_features_ticker_date_version",
        "security_daily_features",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_security_features_ticker_date",
        "security_daily_features",
        ["ticker", "as_of_date"],
    )

    op.drop_table("daily_price_bar_revisions")
    op.drop_table("security_reference_history")
    op.drop_column("financial_facts", "available_at_utc")
