from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.industry_taxonomy import (
    TAXONOMY_VERSION,
    classify_sic,
    industry_hierarchy,
    resolve_industry_groups,
)
from app.models import (
    DailyPriceBar,
    DailyPriceBarRevision,
    Filing,
    FinancialFact,
    IngestionCheckpoint,
    IngestionRun,
    Security,
    SecurityDailyFeature,
    SecurityReferenceHistory,
)
from app.services.massive_ingestion import market_target_date


def _date_value(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be YYYY-MM-DD") from error


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _limit(value: int, maximum: int) -> int:
    if value < 1 or value > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def _sic_prefixes(
    values: list[str] | None,
) -> list[str]:
    prefixes = list(values or [])
    normalized: set[str] = set()
    for value in prefixes:
        prefix = str(value).strip()
        if not prefix.isdigit() or not 1 <= len(prefix) <= 4:
            raise ValueError("SIC prefixes must contain 1 to 4 digits")
        normalized.add(prefix)
    if len(normalized) > 50:
        raise ValueError("at most 50 SIC prefixes may be excluded")
    return sorted(normalized, key=lambda prefix: (len(prefix), prefix))


def _feature_item(feature: SecurityDailyFeature, security: Security) -> dict[str, Any]:
    reference_name = feature.reference_name or security.name
    reference_exchange = feature.reference_primary_exchange
    reference_type = feature.reference_security_type
    reference_sic = feature.reference_sic_code
    reference_sic_description = feature.reference_sic_description
    return {
        "ticker": feature.ticker,
        "company": reference_name,
        "primary_exchange": reference_exchange,
        "security_type": reference_type,
        "active": feature.reference_active,
        "sic_code": reference_sic,
        "sic_description": reference_sic_description,
        "industry_classification": classify_sic(reference_sic),
        "as_of_date": _json_value(feature.as_of_date),
        "price_date": _json_value(feature.price_date),
        "close": _json_value(feature.close),
        "daily_return_pct": _json_value(feature.daily_return_pct),
        "price_change_20d_pct": _json_value(feature.price_change_20d_pct),
        "price_change_12w_pct": _json_value(feature.price_change_12w_pct),
        "drawdown_12w_high_pct": _json_value(feature.drawdown_12w_high_pct),
        "drawdown_52w_pct": _json_value(feature.drawdown_52w_pct),
        "high_20d": _json_value(feature.high_20d),
        "low_20d": _json_value(feature.low_20d),
        "high_60d": _json_value(feature.high_60d),
        "low_60d": _json_value(feature.low_60d),
        "distance_to_20d_high_pct": _json_value(feature.distance_to_20d_high_pct),
        "distance_to_60d_high_pct": _json_value(feature.distance_to_60d_high_pct),
        "atr_14": _json_value(feature.atr_14),
        "atr_14_pct": _json_value(feature.atr_14_pct),
        "overnight_gap_pct": _json_value(feature.overnight_gap_pct),
        "relative_return_20d_vs_qqq_pct": _json_value(
            feature.relative_return_20d_vs_qqq_pct
        ),
        "avg_volume_20d": _json_value(feature.avg_volume_20d),
        "avg_dollar_volume_20d": _json_value(feature.avg_dollar_volume_20d),
        "ema_10": _json_value(feature.ema_10),
        "ema_20": _json_value(feature.ema_20),
        "rsi_14": _json_value(feature.rsi_14),
        "relative_volume_20d": _json_value(feature.relative_volume_20d),
        "revenue_ttm": _json_value(feature.revenue_ttm),
        "revenue_ttm_yoy_pct": _json_value(feature.revenue_ttm_yoy_pct),
        "latest_quarter_revenue": _json_value(feature.latest_quarter_revenue),
        "latest_quarter_revenue_yoy_pct": _json_value(
            feature.latest_quarter_revenue_yoy_pct
        ),
        "revenue_concept": feature.revenue_concept,
        "gross_profit_ttm": _json_value(feature.gross_profit_ttm),
        "gross_margin_ttm_pct": _json_value(feature.gross_margin_ttm_pct),
        "cash_and_short_term_investments": _json_value(
            feature.cash_and_short_term_investments
        ),
        "total_debt": _json_value(feature.total_debt),
        "current_ratio": _json_value(feature.current_ratio),
        "operating_cash_flow_ttm": _json_value(feature.operating_cash_flow_ttm),
        "capital_expenditures_ttm": _json_value(feature.capital_expenditures_ttm),
        "free_cash_flow_ttm": _json_value(feature.free_cash_flow_ttm),
        "cash_runway_months": _json_value(feature.cash_runway_months),
        "shares_outstanding": _json_value(feature.shares_outstanding),
        "share_count_yoy_pct": _json_value(feature.share_count_yoy_pct),
        "approximate_market_cap": _json_value(feature.approximate_market_cap),
        "latest_financial_period_end": _json_value(feature.latest_financial_period_end),
        "latest_source_filing_date": _json_value(feature.latest_source_filing_date),
        "calculation_version": feature.calculation_version,
        "quality_flags": feature.quality_flags or [],
        "source_data_cutoff_utc": _json_value(feature.source_data_cutoff_utc),
        "source_manifest": feature.source_manifest,
    }


def get_security_features(
    session: Session,
    ticker: str,
    as_of_date: str | None = None,
    calculation_version: str | None = None,
) -> dict[str, Any]:
    ticker = ticker.strip().upper()
    requested_date = _date_value(as_of_date, "as_of_date")
    security = session.get(Security, ticker)
    if security is None:
        return {"ticker": ticker, "found": False}
    statement = select(SecurityDailyFeature).where(
        SecurityDailyFeature.ticker == ticker
    )
    if requested_date:
        statement = statement.where(SecurityDailyFeature.as_of_date <= requested_date)
    if calculation_version:
        statement = statement.where(
            SecurityDailyFeature.calculation_version == calculation_version
        )
    feature = session.scalar(
        statement.order_by(
            desc(SecurityDailyFeature.as_of_date),
            desc(SecurityDailyFeature.calculated_at_utc),
        ).limit(1)
    )
    if feature is None:
        return {"ticker": ticker, "found": True, "features_available": False}
    return {
        "ticker": ticker,
        "found": True,
        "features_available": True,
        "interpretation": "deterministic derived fields; no score, rank, or recommendation",
        "item": _feature_item(feature, security),
    }


def query_security_features(
    session: Session,
    as_of_date: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    min_ttm_revenue_growth_pct: float | None = None,
    min_quarter_revenue_growth_pct: float | None = None,
    max_price_change_12w_pct: float | None = None,
    max_drawdown_12w_high_pct: float | None = None,
    max_drawdown_52w_pct: float | None = None,
    min_avg_dollar_volume_20d: float | None = None,
    exclude_healthcare: bool = False,
    nasdaq_nyse_only: bool = True,
    sort_by: str = "avg_dollar_volume_20d",
    descending: bool = True,
    limit: int = 100,
    exclude_sic_prefixes: list[str] | None = None,
    exclude_industry_groups: list[str] | None = None,
    calculation_version: str | None = None,
) -> dict[str, Any]:
    limit = _limit(limit, 500)
    requested_groups = list(exclude_industry_groups or [])
    if exclude_healthcare:
        requested_groups.append("curated:healthcare")
    resolved_groups, group_prefixes = resolve_industry_groups(requested_groups)
    excluded_prefixes = _sic_prefixes(list(exclude_sic_prefixes or []) + group_prefixes)
    resolved_exclusions = [
        {
            "key": group["key"],
            "label": group["label"],
            "level": group["level"],
            "sic_prefixes": list(group["sic_prefixes"]),
        }
        for group in resolved_groups
    ]
    requested_date = _date_value(as_of_date, "as_of_date")
    date_statement = select(func.max(SecurityDailyFeature.as_of_date))
    if requested_date:
        date_statement = date_statement.where(
            SecurityDailyFeature.as_of_date <= requested_date
        )
    if calculation_version:
        date_statement = date_statement.where(
            SecurityDailyFeature.calculation_version == calculation_version
        )
    effective_date = session.scalar(date_statement)
    if effective_date is None:
        return {
            "as_of_date": None,
            "calculation_version": calculation_version,
            "count": 0,
            "items": [],
            "excluded_sic_prefixes": excluded_prefixes,
            "excluded_industry_groups": resolved_exclusions,
            "industry_taxonomy_version": TAXONOMY_VERSION,
            "unknown_sic_codes_retained": True,
            "interpretation": "derived features have not been calculated",
        }
    effective_version = calculation_version or session.scalar(
        select(SecurityDailyFeature.calculation_version)
        .where(SecurityDailyFeature.as_of_date == effective_date)
        .order_by(desc(SecurityDailyFeature.calculated_at_utc))
        .limit(1)
    )
    statement = (
        select(SecurityDailyFeature, Security)
        .join(Security, Security.ticker == SecurityDailyFeature.ticker)
        .where(
            SecurityDailyFeature.as_of_date == effective_date,
            SecurityDailyFeature.calculation_version == effective_version,
            SecurityDailyFeature.price_date == effective_date,
            SecurityDailyFeature.reference_active.is_(True),
        )
    )
    if nasdaq_nyse_only:
        statement = statement.where(
            SecurityDailyFeature.reference_primary_exchange.in_(("XNAS", "XNYS"))
        )
    if excluded_prefixes:
        normalized_sic = func.lpad(SecurityDailyFeature.reference_sic_code, 4, "0")
        excluded_industries = or_(
            *(normalized_sic.like(f"{prefix}%") for prefix in excluded_prefixes)
        )
        statement = statement.where(
            or_(
                SecurityDailyFeature.reference_sic_code.is_(None),
                ~excluded_industries,
            )
        )

    filters = (
        (min_price, SecurityDailyFeature.close, ">="),
        (max_price, SecurityDailyFeature.close, "<="),
        (min_market_cap, SecurityDailyFeature.approximate_market_cap, ">="),
        (max_market_cap, SecurityDailyFeature.approximate_market_cap, "<="),
        (min_ttm_revenue_growth_pct, SecurityDailyFeature.revenue_ttm_yoy_pct, ">="),
        (
            min_quarter_revenue_growth_pct,
            SecurityDailyFeature.latest_quarter_revenue_yoy_pct,
            ">=",
        ),
        (max_price_change_12w_pct, SecurityDailyFeature.price_change_12w_pct, "<="),
        (
            max_drawdown_12w_high_pct,
            SecurityDailyFeature.drawdown_12w_high_pct,
            "<=",
        ),
        (max_drawdown_52w_pct, SecurityDailyFeature.drawdown_52w_pct, "<="),
        (
            min_avg_dollar_volume_20d,
            SecurityDailyFeature.avg_dollar_volume_20d,
            ">=",
        ),
    )
    for value, column, operator in filters:
        if value is not None:
            statement = statement.where(
                column >= value if operator == ">=" else column <= value
            )

    sort_columns = {
        "ticker": SecurityDailyFeature.ticker,
        "close": SecurityDailyFeature.close,
        "daily_return": SecurityDailyFeature.daily_return_pct,
        "market_cap": SecurityDailyFeature.approximate_market_cap,
        "ttm_revenue_growth": SecurityDailyFeature.revenue_ttm_yoy_pct,
        "quarter_revenue_growth": SecurityDailyFeature.latest_quarter_revenue_yoy_pct,
        "price_change_12w": SecurityDailyFeature.price_change_12w_pct,
        "drawdown_12w_high": SecurityDailyFeature.drawdown_12w_high_pct,
        "drawdown_52w": SecurityDailyFeature.drawdown_52w_pct,
        "avg_dollar_volume_20d": SecurityDailyFeature.avg_dollar_volume_20d,
        "rsi_14": SecurityDailyFeature.rsi_14,
        "atr_14_pct": SecurityDailyFeature.atr_14_pct,
        "relative_return_20d_vs_qqq": SecurityDailyFeature.relative_return_20d_vs_qqq_pct,
    }
    if sort_by not in sort_columns:
        raise ValueError(f"sort_by must be one of: {', '.join(sorted(sort_columns))}")
    sort_column = sort_columns[sort_by]
    order = (
        sort_column.desc().nullslast() if descending else sort_column.asc().nullslast()
    )
    rows = session.execute(
        statement.order_by(order, SecurityDailyFeature.ticker).limit(limit)
    ).all()
    return {
        "as_of_date": effective_date.isoformat(),
        "calculation_version": effective_version,
        "count": len(rows),
        "limit": limit,
        "filters_are_user_supplied": True,
        "excluded_sic_prefixes": excluded_prefixes,
        "excluded_industry_groups": resolved_exclusions,
        "industry_taxonomy_version": TAXONOMY_VERSION,
        "unknown_sic_codes_retained": True,
        "interpretation": "neutral filtering of deterministic fields; no score, rank, or recommendation",
        "items": [_feature_item(feature, security) for feature, security in rows],
    }


def get_security_history(
    session: Session,
    ticker: str,
    limit: int = 100,
) -> dict[str, Any]:
    ticker = ticker.strip().upper()
    limit = _limit(limit, 500)
    rows = session.scalars(
        select(SecurityReferenceHistory)
        .where(SecurityReferenceHistory.ticker == ticker)
        .order_by(desc(SecurityReferenceHistory.observed_at_utc))
        .limit(limit)
    ).all()
    return {
        "ticker": ticker,
        "count": len(rows),
        "items": [
            {
                "source": row.source,
                "record_hash": row.record_hash,
                "observed_at_utc": row.observed_at_utc.isoformat(),
                "snapshot": row.snapshot,
            }
            for row in rows
        ],
    }


def get_price_revisions(
    session: Session,
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    ticker = ticker.strip().upper()
    limit = _limit(limit, 2000)
    start = _date_value(start_date, "start_date")
    end = _date_value(end_date, "end_date")
    statement = select(DailyPriceBarRevision).where(
        DailyPriceBarRevision.ticker == ticker
    )
    if start:
        statement = statement.where(DailyPriceBarRevision.trade_date >= start)
    if end:
        statement = statement.where(DailyPriceBarRevision.trade_date <= end)
    rows = session.scalars(
        statement.order_by(
            desc(DailyPriceBarRevision.trade_date),
            desc(DailyPriceBarRevision.observed_at_utc),
        ).limit(limit)
    ).all()
    return {
        "ticker": ticker,
        "count": len(rows),
        "items": [
            {
                "trade_date": row.trade_date.isoformat(),
                "open": _json_value(row.open),
                "high": _json_value(row.high),
                "low": _json_value(row.low),
                "close": _json_value(row.close),
                "volume": _json_value(row.volume),
                "vwap": _json_value(row.vwap),
                "transactions": row.transactions,
                "adjusted": row.adjusted,
                "source": row.source,
                "source_timestamp_ms": row.source_timestamp_ms,
                "record_hash": row.record_hash,
                "observed_at_utc": row.observed_at_utc.isoformat(),
            }
            for row in rows
        ],
    }


def search_securities(
    session: Session,
    query: str,
    active_only: bool = True,
    limit: int = 20,
) -> dict[str, Any]:
    limit = _limit(limit, 100)
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    pattern = f"%{query}%"
    statement = select(Security).where(
        Security.ticker.ilike(pattern) | Security.name.ilike(pattern)
    )
    if active_only:
        statement = statement.where(Security.active.is_(True))
    rows = session.scalars(statement.order_by(Security.ticker).limit(limit)).all()
    return {
        "query": query,
        "items": [
            {
                "ticker": row.ticker,
                "name": row.name,
                "primary_exchange": row.primary_exchange,
                "security_type": row.security_type,
                "active": row.active,
                "cik": row.cik,
                "sic_code": row.sic_code,
                "sic_description": row.sic_description,
                "industry_classification": classify_sic(row.sic_code),
            }
            for row in rows
        ],
    }


def lookup_security(session: Session, ticker: str) -> dict[str, Any]:
    ticker = ticker.strip().upper()
    row = session.get(Security, ticker)
    if row is None:
        return {"ticker": ticker, "found": False}
    latest_trade_date = session.scalar(
        select(func.max(DailyPriceBar.trade_date)).where(DailyPriceBar.ticker == ticker)
    )
    latest_filing_date = (
        session.scalar(select(func.max(Filing.filed_date)).where(Filing.cik == row.cik))
        if row.cik
        else None
    )
    return {
        "ticker": row.ticker,
        "found": True,
        "name": row.name,
        "market": row.market,
        "locale": row.locale,
        "currency": row.currency,
        "primary_exchange": row.primary_exchange,
        "security_type": row.security_type,
        "active": row.active,
        "cik": row.cik,
        "composite_figi": row.composite_figi,
        "share_class_figi": row.share_class_figi,
        "sic_code": row.sic_code,
        "sic_description": row.sic_description,
        "industry_classification": classify_sic(row.sic_code),
        "fiscal_year_end": row.fiscal_year_end,
        "state_of_incorporation": row.state_of_incorporation,
        "latest_trade_date": _json_value(latest_trade_date),
        "latest_filing_date": _json_value(latest_filing_date),
        "sources": ["massive", "sec-edgar"],
    }


def get_industry_hierarchy() -> dict[str, Any]:
    """Return the complete readable SIC hierarchy and curated cross-division groups."""
    return industry_hierarchy()


def get_price_history(
    session: Session,
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    ticker = ticker.strip().upper()
    limit = _limit(limit, 2000)
    start = _date_value(start_date, "start_date")
    end = _date_value(end_date, "end_date")
    if start and end and start > end:
        raise ValueError("start_date must be on or before end_date")
    if session.get(Security, ticker) is None:
        return {"ticker": ticker, "found": False, "source": "massive", "items": []}
    statement = select(DailyPriceBar).where(DailyPriceBar.ticker == ticker)
    if start:
        statement = statement.where(DailyPriceBar.trade_date >= start)
    if end:
        statement = statement.where(DailyPriceBar.trade_date <= end)
    rows = session.scalars(
        statement.order_by(desc(DailyPriceBar.trade_date)).limit(limit)
    ).all()
    rows.reverse()
    return {
        "ticker": ticker,
        "found": True,
        "source": "massive",
        "adjustment": "provider-adjusted when adjusted=true",
        "items": [
            {
                "trade_date": _json_value(row.trade_date),
                "open": _json_value(row.open),
                "high": _json_value(row.high),
                "low": _json_value(row.low),
                "close": _json_value(row.close),
                "volume": _json_value(row.volume),
                "vwap": _json_value(row.vwap),
                "transactions": row.transactions,
                "adjusted": row.adjusted,
                "ingested_at_utc": _json_value(row.ingested_at_utc),
            }
            for row in rows
        ],
    }


def get_financial_facts(
    session: Session,
    ticker: str,
    concepts: list[str] | None = None,
    forms: list[str] | None = None,
    filed_after: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    ticker = ticker.strip().upper()
    limit = _limit(limit, 1000)
    filed_after_date = _date_value(filed_after, "filed_after")
    security = session.get(Security, ticker)
    if security is None or not security.cik:
        return {
            "ticker": ticker,
            "found": security is not None,
            "cik": security.cik if security else None,
            "source": "sec-edgar",
            "items": [],
        }
    statement = select(FinancialFact).where(FinancialFact.cik == security.cik)
    if concepts:
        statement = statement.where(FinancialFact.concept.in_(concepts))
    if forms:
        statement = statement.where(
            FinancialFact.form.in_([form.upper() for form in forms])
        )
    if filed_after_date:
        statement = statement.where(FinancialFact.filed_date >= filed_after_date)
    rows = session.scalars(
        statement.order_by(
            desc(FinancialFact.period_end), desc(FinancialFact.filed_date)
        ).limit(limit)
    ).all()
    return {
        "ticker": ticker,
        "found": True,
        "cik": security.cik,
        "source": "sec-edgar",
        "interpretation": "reported source facts; no TTM or tag normalization applied",
        "items": [
            {
                "taxonomy": row.taxonomy,
                "concept": row.concept,
                "label": row.label,
                "unit": row.unit,
                "value": _json_value(row.value),
                "period_start": _json_value(row.period_start),
                "period_end": _json_value(row.period_end),
                "filed_date": _json_value(row.filed_date),
                "available_at_utc": _json_value(row.available_at_utc),
                "form": row.form,
                "fiscal_year": row.fiscal_year,
                "fiscal_period": row.fiscal_period,
                "frame": row.frame,
                "accession_number": row.accession_number,
            }
            for row in rows
        ],
    }


def get_filings(
    session: Session,
    ticker: str,
    forms: list[str] | None = None,
    filed_after: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    ticker = ticker.strip().upper()
    limit = _limit(limit, 500)
    filed_after_date = _date_value(filed_after, "filed_after")
    security = session.get(Security, ticker)
    if security is None or not security.cik:
        return {
            "ticker": ticker,
            "found": security is not None,
            "cik": security.cik if security else None,
            "source": "sec-edgar",
            "items": [],
        }
    statement = select(Filing).where(Filing.cik == security.cik)
    if forms:
        statement = statement.where(Filing.form.in_([form.upper() for form in forms]))
    if filed_after_date:
        statement = statement.where(Filing.filed_date >= filed_after_date)
    rows = session.scalars(
        statement.order_by(desc(Filing.filed_date)).limit(limit)
    ).all()
    return {
        "ticker": ticker,
        "found": True,
        "cik": security.cik,
        "source": "sec-edgar",
        "items": [
            {
                "accession_number": row.accession_number,
                "form": row.form,
                "filed_date": _json_value(row.filed_date),
                "report_date": _json_value(row.report_date),
                "accepted_at": _json_value(row.accepted_at),
                "primary_document": row.primary_document,
                "description": row.primary_doc_description,
                "items": row.items,
                "is_xbrl": row.is_xbrl,
                "is_inline_xbrl": row.is_inline_xbrl,
                "source_url": row.source_url,
            }
            for row in rows
        ],
    }


def get_data_freshness(
    session: Session,
    settings: Settings | None = None,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    timezone = ZoneInfo(settings.timezone)
    if current_time is None:
        local_time = datetime.now(timezone)
    elif current_time.tzinfo is None:
        local_time = current_time.replace(tzinfo=timezone)
    else:
        local_time = current_time.astimezone(timezone)
    expected_market_date = market_target_date(
        local_time.date(), settings.massive_market_lag_days
    )
    latest_trade_date = session.scalar(select(func.max(DailyPriceBar.trade_date)))
    latest_sec_filing_date = session.scalar(select(func.max(Filing.filed_date)))
    latest_feature_date = session.scalar(
        select(func.max(SecurityDailyFeature.as_of_date))
    )
    job_names = session.scalars(select(IngestionRun.job_name).distinct()).all()
    jobs: list[dict[str, Any]] = []
    for job_name in sorted(job_names):
        run = session.scalar(
            select(IngestionRun)
            .where(IngestionRun.job_name == job_name)
            .order_by(desc(IngestionRun.started_at_utc))
            .limit(1)
        )
        if run:
            jobs.append(
                {
                    "job_name": run.job_name,
                    "source": run.source,
                    "status": run.status,
                    "started_at_utc": _json_value(run.started_at_utc),
                    "completed_at_utc": _json_value(run.completed_at_utc),
                    "records_seen": run.records_seen,
                    "records_written": run.records_written,
                    "details": run.details,
                    "error_message": run.error_message,
                }
            )
    checkpoints = session.scalars(
        select(IngestionCheckpoint).order_by(IngestionCheckpoint.job_name)
    ).all()
    feature_job = next(
        (job for job in jobs if job["job_name"] == "derived_features"), None
    )
    market_is_current = latest_trade_date == expected_market_date
    features_are_current = (
        latest_feature_date == expected_market_date
        and latest_feature_date == latest_trade_date
        and feature_job is not None
        and feature_job["status"] == "succeeded"
    )
    return {
        "checked_at_local": local_time.isoformat(),
        "timezone": settings.timezone,
        "expected_market_date": expected_market_date.isoformat(),
        "latest_trade_date": _json_value(latest_trade_date),
        "latest_sec_filing_date": _json_value(latest_sec_filing_date),
        "latest_feature_date": _json_value(latest_feature_date),
        "market_is_current": market_is_current,
        "features_are_current": features_are_current,
        "ready_for_screening": market_is_current and features_are_current,
        "schedules": {
            "market_sync_cron": settings.market_sync_cron,
            "feature_sync_cron": settings.feature_sync_cron,
            "sec_sync_cron": settings.sec_sync_cron,
            "reference_sync_cron": settings.reference_sync_cron,
            "market_lag_days": settings.massive_market_lag_days,
        },
        "checkpoints": [
            {
                "job_name": checkpoint.job_name,
                "checkpoint_date": _json_value(checkpoint.checkpoint_date),
                "updated_at_utc": _json_value(checkpoint.updated_at_utc),
                "details": checkpoint.details,
            }
            for checkpoint in checkpoints
        ],
        "jobs": jobs,
    }
