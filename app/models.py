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


class SecurityDailyFeature(Base):
    __tablename__ = "security_daily_features"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "as_of_date", name="uq_security_features_ticker_date"
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
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    price_change_12w_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    drawdown_52w_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
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
