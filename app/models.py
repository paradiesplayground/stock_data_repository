from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Security(Base):
    __tablename__ = "securities"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(512))
    market: Mapped[str | None] = mapped_column(String(32))
    locale: Mapped[str | None] = mapped_column(String(16))
    currency: Mapped[str | None] = mapped_column(String(16))
    primary_exchange: Mapped[str | None] = mapped_column(String(32), index=True)
    security_type: Mapped[str | None] = mapped_column(String(32), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    cik: Mapped[str | None] = mapped_column(String(10), index=True)
    composite_figi: Mapped[str | None] = mapped_column(String(32))
    share_class_figi: Mapped[str | None] = mapped_column(String(32))
    sic_code: Mapped[str | None] = mapped_column(String(8), index=True)
    sic_description: Mapped[str | None] = mapped_column(String(255))
    fiscal_year_end: Mapped[str | None] = mapped_column(String(4))
    state_of_incorporation: Mapped[str | None] = mapped_column(String(8))
    last_updated_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    price_bars: Mapped[list["DailyPriceBar"]] = relationship(back_populates="security")


class SecurityReferenceHistory(Base):
    __tablename__ = "security_reference_history"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "source", "record_hash", name="uq_security_history_record"
        ),
        Index("ix_security_history_ticker_observed", "ticker", "observed_at_utc"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    record_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    observed_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DailyPriceBar(Base):
    __tablename__ = "daily_price_bars"
    __table_args__ = (
        UniqueConstraint("ticker", "trade_date", name="uq_daily_price_ticker_date"),
        Index("ix_daily_price_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(
        ForeignKey("securities.ticker", ondelete="CASCADE")
    )
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 4))
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    transactions: Mapped[int | None] = mapped_column(BigInteger)
    adjusted: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="massive")
    source_timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    ingested_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    security: Mapped[Security] = relationship(back_populates="price_bars")


class DailyPriceBarRevision(Base):
    __tablename__ = "daily_price_bar_revisions"
    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "trade_date",
            "source",
            "record_hash",
            name="uq_daily_price_revision_record",
        ),
        Index("ix_daily_price_revision_ticker_date", "ticker", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32))
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 4))
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    transactions: Mapped[int | None] = mapped_column(BigInteger)
    adjusted: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="massive")
    source_timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    record_hash: Mapped[str] = mapped_column(String(64))
    observed_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SecurityDailyFeature(Base):
    __tablename__ = "security_daily_features"
    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "as_of_date",
            "calculation_version",
            name="uq_security_features_ticker_date_version",
        ),
        Index("ix_security_features_date", "as_of_date"),
        Index("ix_security_features_liquidity", "as_of_date", "avg_dollar_volume_20d"),
        Index("ix_security_features_growth", "as_of_date", "revenue_ttm_yoy_pct"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(
        ForeignKey("securities.ticker", ondelete="CASCADE")
    )
    as_of_date: Mapped[date] = mapped_column(Date)
    price_date: Mapped[date] = mapped_column(Date)
    reference_name: Mapped[str | None] = mapped_column(String(512))
    reference_primary_exchange: Mapped[str | None] = mapped_column(String(32))
    reference_security_type: Mapped[str | None] = mapped_column(String(32))
    reference_active: Mapped[bool | None] = mapped_column(Boolean)
    reference_sic_code: Mapped[str | None] = mapped_column(String(8))
    reference_sic_description: Mapped[str | None] = mapped_column(String(255))
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    price_change_20d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    price_change_12w_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    drawdown_12w_high_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    drawdown_52w_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    high_20d: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    low_20d: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    high_60d: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    low_60d: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    distance_to_20d_high_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    distance_to_60d_high_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    atr_14: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    atr_14_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    overnight_gap_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    relative_return_20d_vs_qqq_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8)
    )
    avg_volume_20d: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    avg_dollar_volume_20d: Mapped[Decimal | None] = mapped_column(Numeric(28, 4))
    ema_10: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    ema_20: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    rsi_14: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    relative_volume_20d: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))

    revenue_ttm: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    revenue_ttm_yoy_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    latest_quarter_revenue: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    latest_quarter_revenue_yoy_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8)
    )
    revenue_concept: Mapped[str | None] = mapped_column(String(255))
    gross_profit_ttm: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    gross_margin_ttm_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))

    cash_and_short_term_investments: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 8)
    )
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    current_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    operating_cash_flow_ttm: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    capital_expenditures_ttm: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    free_cash_flow_ttm: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    cash_runway_months: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    share_count_yoy_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    approximate_market_cap: Mapped[Decimal | None] = mapped_column(Numeric(38, 4))

    latest_financial_period_end: Mapped[date | None] = mapped_column(Date)
    latest_source_filing_date: Mapped[date | None] = mapped_column(Date)
    calculation_version: Mapped[str] = mapped_column(String(32))
    quality_flags: Mapped[list[str] | None] = mapped_column(JSONB)
    source_data_cutoff_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    source_manifest: Mapped[dict | None] = mapped_column(JSONB)
    calculated_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (
        Index("ix_facts_cik_concept_end", "cik", "concept", "period_end"),
        Index("ix_facts_accession", "accession_number"),
    )

    fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cik: Mapped[str] = mapped_column(String(10), index=True)
    taxonomy: Mapped[str] = mapped_column(String(64))
    concept: Mapped[str] = mapped_column(String(255), index=True)
    label: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(64))
    value: Mapped[Decimal] = mapped_column(Numeric(38, 8))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    filed_date: Mapped[date | None] = mapped_column(Date)
    available_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    form: Mapped[str | None] = mapped_column(String(32))
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    fiscal_period: Mapped[str | None] = mapped_column(String(16))
    frame: Mapped[str | None] = mapped_column(String(32))
    accession_number: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32), default="sec-edgar")
    ingested_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (Index("ix_filings_cik_filed", "cik", "filed_date"),)

    accession_number: Mapped[str] = mapped_column(String(32), primary_key=True)
    cik: Mapped[str] = mapped_column(String(10), index=True)
    form: Mapped[str] = mapped_column(String(32), index=True)
    filed_date: Mapped[date] = mapped_column(Date)
    report_date: Mapped[date | None] = mapped_column(Date)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_document: Mapped[str | None] = mapped_column(String(512))
    primary_doc_description: Mapped[str | None] = mapped_column(String(1024))
    file_number: Mapped[str | None] = mapped_column(String(128))
    film_number: Mapped[str | None] = mapped_column(String(64))
    items: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    is_xbrl: Mapped[bool | None] = mapped_column(Boolean)
    is_inline_xbrl: Mapped[bool | None] = mapped_column(Boolean)
    source_url: Mapped[str | None] = mapped_column(Text)
    ingested_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingestion_job_started", "job_name", "started_at_utc"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_seen: Mapped[int] = mapped_column(BigInteger, default=0)
    records_written: Mapped[int] = mapped_column(BigInteger, default=0)
    source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)


class IngestionCheckpoint(Base):
    __tablename__ = "ingestion_checkpoints"

    job_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_date: Mapped[date] = mapped_column(Date)
    details: Mapped[dict | None] = mapped_column(JSONB)
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StrategyDefinition(Base):
    __tablename__ = "strategy_definitions"
    __table_args__ = (
        UniqueConstraint("strategy_key", "version", name="uq_strategy_key_version"),
        {"schema": "strategy_tracking"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_key: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(255))
    configuration: Mapped[dict] = mapped_column(JSONB)
    skill_fingerprint: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StrategyRun(Base):
    __tablename__ = "strategy_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_strategy_run_idempotency"),
        Index(
            "ix_strategy_runs_definition_date", "strategy_definition_id", "as_of_date"
        ),
        {"schema": "strategy_tracking"},
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy_definition_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_tracking.strategy_definitions.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255))
    as_of_date: Mapped[date] = mapped_column(Date)
    run_type: Mapped[str] = mapped_column(String(32), index=True)
    feature_calculation_version: Mapped[str | None] = mapped_column(String(32))
    data_cutoff_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filters: Mapped[dict] = mapped_column(JSONB)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    generated_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StrategyCandidate(Base):
    __tablename__ = "strategy_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "ticker", name="uq_strategy_candidate_run_ticker"),
        Index("ix_strategy_candidates_ticker", "ticker"),
        {"schema": "strategy_tracking"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_tracking.strategy_runs.run_id", ondelete="CASCADE")
    )
    ticker: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(32))
    action: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    score_components: Mapped[dict | None] = mapped_column(JSONB)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    reasons: Mapped[list | None] = mapped_column(JSONB)
    trade_plan: Mapped[dict | None] = mapped_column(JSONB)
    payload: Mapped[dict | None] = mapped_column(JSONB)


class StrategyEvidence(Base):
    __tablename__ = "strategy_evidence"
    __table_args__ = (
        Index("ix_strategy_evidence_run_ticker", "run_id", "ticker"),
        {"schema": "strategy_tracking"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_tracking.strategy_runs.run_id", ondelete="CASCADE")
    )
    ticker: Mapped[str | None] = mapped_column(String(32))
    evidence_type: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)
    accession_number: Mapped[str | None] = mapped_column(String(32))
    published_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)


class StrategyOutcomeObservation(Base):
    __tablename__ = "strategy_outcome_observations"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "ticker",
            "observation_date",
            "horizon",
            name="uq_strategy_outcome_observation",
        ),
        Index("ix_strategy_outcomes_ticker_date", "ticker", "observation_date"),
        {"schema": "strategy_tracking"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_tracking.strategy_runs.run_id", ondelete="CASCADE")
    )
    ticker: Mapped[str] = mapped_column(String(32))
    observation_date: Mapped[date] = mapped_column(Date)
    horizon: Mapped[str] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(32))
    metrics: Mapped[dict] = mapped_column(JSONB)
    execution_assumptions: Mapped[dict | None] = mapped_column(JSONB)
    observed_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StrategySimulationRun(Base):
    __tablename__ = "strategy_simulation_runs"
    __table_args__ = (
        UniqueConstraint("scenario_key", name="uq_strategy_simulation_scenario"),
        Index(
            "ix_strategy_simulations_definition_dates",
            "strategy_definition_id",
            "start_date",
            "end_date",
        ),
        Index("ix_strategy_simulation_runs_status", "status"),
        {"schema": "strategy_tracking"},
    )

    simulation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy_definition_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_tracking.strategy_definitions.id", ondelete="RESTRICT")
    )
    scenario_key: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    feature_calculation_version: Mapped[str] = mapped_column(String(32))
    source_runs_hash: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32))
    summary: Mapped[dict | None] = mapped_column(JSONB)
    generated_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StrategySimulationTrade(Base):
    __tablename__ = "strategy_simulation_trades"
    __table_args__ = (
        UniqueConstraint(
            "simulation_id",
            "source_run_id",
            "ticker",
            name="uq_strategy_simulation_trade_signal",
        ),
        Index("ix_strategy_simulation_trades_ticker", "ticker"),
        Index("ix_strategy_simulation_trades_status", "status"),
        {"schema": "strategy_tracking"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(
        ForeignKey(
            "strategy_tracking.strategy_simulation_runs.simulation_id",
            ondelete="CASCADE",
        )
    )
    source_run_id: Mapped[str] = mapped_column(String(36))
    ticker: Mapped[str] = mapped_column(String(32))
    signal_date: Mapped[date] = mapped_column(Date)
    order_expiration_date: Mapped[date | None] = mapped_column(Date)
    entry_date: Mapped[date | None] = mapped_column(Date)
    exit_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32))
    initial_shares: Mapped[int | None] = mapped_column(BigInteger)
    remaining_shares: Mapped[int | None] = mapped_column(BigInteger)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    initial_stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    target_one_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    target_two_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    planned_risk: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    realized_r: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    holding_sessions: Mapped[int | None] = mapped_column(Integer)
    exit_reason: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSONB)


class StrategySimulationEquityPoint(Base):
    __tablename__ = "strategy_simulation_equity"
    __table_args__ = (
        UniqueConstraint(
            "simulation_id",
            "market_date",
            name="uq_strategy_simulation_equity_date",
        ),
        {"schema": "strategy_tracking"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(
        ForeignKey(
            "strategy_tracking.strategy_simulation_runs.simulation_id",
            ondelete="CASCADE",
        )
    )
    market_date: Mapped[date] = mapped_column(Date)
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    open_positions: Mapped[int] = mapped_column(Integer)
    planned_open_risk: Mapped[Decimal] = mapped_column(Numeric(20, 4))
