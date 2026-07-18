from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models import (
    DailyPriceBar,
    Filing,
    FinancialFact,
    IngestionCheckpoint,
    IngestionRun,
    Security,
)


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
        "fiscal_year_end": row.fiscal_year_end,
        "state_of_incorporation": row.state_of_incorporation,
        "latest_trade_date": _json_value(latest_trade_date),
        "latest_filing_date": _json_value(latest_filing_date),
        "sources": ["massive", "sec-edgar"],
    }


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
    rows = session.scalars(statement.order_by(desc(DailyPriceBar.trade_date)).limit(limit)).all()
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
        statement = statement.where(FinancialFact.form.in_([form.upper() for form in forms]))
    if filed_after_date:
        statement = statement.where(FinancialFact.filed_date >= filed_after_date)
    rows = session.scalars(
        statement.order_by(desc(FinancialFact.period_end), desc(FinancialFact.filed_date)).limit(limit)
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
    rows = session.scalars(statement.order_by(desc(Filing.filed_date)).limit(limit)).all()
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


def get_data_freshness(session: Session) -> dict[str, Any]:
    latest_trade_date = session.scalar(select(func.max(DailyPriceBar.trade_date)))
    latest_sec_filing_date = session.scalar(select(func.max(Filing.filed_date)))
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
                    "status": run.status,
                    "started_at_utc": _json_value(run.started_at_utc),
                    "completed_at_utc": _json_value(run.completed_at_utc),
                    "records_seen": run.records_seen,
                    "records_written": run.records_written,
                    "details": run.details,
                    "error_message": run.error_message,
                }
            )
    checkpoints = session.scalars(select(IngestionCheckpoint).order_by(IngestionCheckpoint.job_name)).all()
    return {
        "latest_trade_date": _json_value(latest_trade_date),
        "latest_sec_filing_date": _json_value(latest_sec_filing_date),
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
