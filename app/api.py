from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    DailyPriceBar,
    Filing,
    FinancialFact,
    IngestionCheckpoint,
    IngestionRun,
    Security,
)
from app.security import require_api_token

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])
DbSession = Annotated[Session, Depends(get_db)]


def _security_or_404(session: Session, ticker: str) -> Security:
    security = session.get(Security, ticker.upper())
    if security is None:
        raise HTTPException(status_code=404, detail="Ticker not found")
    return security


@router.get("/freshness")
def freshness(session: DbSession) -> dict[str, object]:
    jobs = session.scalars(select(IngestionRun.job_name).distinct()).all()
    latest: list[dict[str, object]] = []
    for job in sorted(jobs):
        run = session.scalar(
            select(IngestionRun)
            .where(IngestionRun.job_name == job)
            .order_by(desc(IngestionRun.started_at_utc))
            .limit(1)
        )
        if run:
            latest.append(
                {
                    "job_name": run.job_name,
                    "source": run.source,
                    "status": run.status,
                    "started_at_utc": run.started_at_utc,
                    "completed_at_utc": run.completed_at_utc,
                    "records_seen": run.records_seen,
                    "records_written": run.records_written,
                    "details": run.details,
                    "error_message": run.error_message,
                }
            )
    latest_trade_date = session.scalar(select(func.max(DailyPriceBar.trade_date)))
    latest_sec_filed = session.scalar(select(func.max(Filing.filed_date)))
    checkpoints = session.scalars(select(IngestionCheckpoint).order_by(IngestionCheckpoint.job_name)).all()
    return {
        "latest_trade_date": latest_trade_date,
        "latest_sec_filing_date": latest_sec_filed,
        "checkpoints": [
            {
                "job_name": checkpoint.job_name,
                "checkpoint_date": checkpoint.checkpoint_date,
                "updated_at_utc": checkpoint.updated_at_utc,
                "details": checkpoint.details,
            }
            for checkpoint in checkpoints
        ],
        "jobs": latest,
    }


@router.get("/securities")
def list_securities(
    session: DbSession,
    search: str | None = None,
    active: bool | None = True,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    statement = select(Security)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            Security.ticker.ilike(pattern) | Security.name.ilike(pattern)
        )
    if active is not None:
        statement = statement.where(Security.active == active)
    statement = statement.order_by(Security.ticker).offset(offset).limit(limit)
    rows = session.scalars(statement).all()
    return {
        "items": [
            {
                "ticker": row.ticker,
                "name": row.name,
                "market": row.market,
                "locale": row.locale,
                "currency": row.currency,
                "primary_exchange": row.primary_exchange,
                "security_type": row.security_type,
                "active": row.active,
                "cik": row.cik,
                "sic_code": row.sic_code,
                "sic_description": row.sic_description,
            }
            for row in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/securities/{ticker}")
def get_security(ticker: str, session: DbSession) -> dict[str, object]:
    row = _security_or_404(session, ticker)
    latest_trade_date = session.scalar(
        select(func.max(DailyPriceBar.trade_date)).where(DailyPriceBar.ticker == row.ticker)
    )
    return {
        "ticker": row.ticker,
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
        "latest_trade_date": latest_trade_date,
        "source": "massive",
    }


@router.get("/securities/{ticker}/prices")
def get_prices(
    ticker: str,
    session: DbSession,
    start: date | None = None,
    end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict[str, object]:
    security = _security_or_404(session, ticker)
    statement = select(DailyPriceBar).where(DailyPriceBar.ticker == security.ticker)
    if start:
        statement = statement.where(DailyPriceBar.trade_date >= start)
    if end:
        statement = statement.where(DailyPriceBar.trade_date <= end)
    rows = session.scalars(statement.order_by(desc(DailyPriceBar.trade_date)).limit(limit)).all()
    return {
        "ticker": security.ticker,
        "source": "massive",
        "items": [
            {
                "trade_date": row.trade_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "vwap": row.vwap,
                "transactions": row.transactions,
                "adjusted": row.adjusted,
                "ingested_at_utc": row.ingested_at_utc,
            }
            for row in rows
        ],
    }


@router.get("/securities/{ticker}/facts")
def get_facts(
    ticker: str,
    session: DbSession,
    concept: str | None = None,
    form: str | None = None,
    filed_after: date | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict[str, object]:
    security = _security_or_404(session, ticker)
    if not security.cik:
        return {"ticker": security.ticker, "cik": None, "source": "sec-edgar", "items": []}
    statement = select(FinancialFact).where(FinancialFact.cik == security.cik)
    if concept:
        statement = statement.where(FinancialFact.concept == concept)
    if form:
        statement = statement.where(FinancialFact.form == form)
    if filed_after:
        statement = statement.where(FinancialFact.filed_date >= filed_after)
    rows = session.scalars(
        statement.order_by(desc(FinancialFact.period_end), desc(FinancialFact.filed_date)).limit(limit)
    ).all()
    return {
        "ticker": security.ticker,
        "cik": security.cik,
        "source": "sec-edgar",
        "items": [
            {
                "taxonomy": row.taxonomy,
                "concept": row.concept,
                "label": row.label,
                "unit": row.unit,
                "value": row.value,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "filed_date": row.filed_date,
                "form": row.form,
                "fiscal_year": row.fiscal_year,
                "fiscal_period": row.fiscal_period,
                "frame": row.frame,
                "accession_number": row.accession_number,
            }
            for row in rows
        ],
    }


@router.get("/securities/{ticker}/filings")
def get_filings(
    ticker: str,
    session: DbSession,
    form: str | None = None,
    filed_after: date | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, object]:
    security = _security_or_404(session, ticker)
    if not security.cik:
        return {"ticker": security.ticker, "cik": None, "source": "sec-edgar", "items": []}
    statement = select(Filing).where(Filing.cik == security.cik)
    if form:
        statement = statement.where(Filing.form == form)
    if filed_after:
        statement = statement.where(Filing.filed_date >= filed_after)
    rows = session.scalars(statement.order_by(desc(Filing.filed_date)).limit(limit)).all()
    return {
        "ticker": security.ticker,
        "cik": security.cik,
        "source": "sec-edgar",
        "items": [
            {
                "accession_number": row.accession_number,
                "form": row.form,
                "filed_date": row.filed_date,
                "report_date": row.report_date,
                "accepted_at": row.accepted_at,
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
