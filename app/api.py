from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.industry_taxonomy import classify_sic
from app.mcp_queries import (
    get_data_freshness as query_data_freshness,
    get_industry_hierarchy as query_industry_hierarchy,
    get_security_features as query_security_features_for_ticker,
    query_security_features as query_feature_rows,
)
from app.models import (
    DailyPriceBar,
    Filing,
    FinancialFact,
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
    return query_data_freshness(session)


@router.get("/industry-hierarchy")
def industry_hierarchy() -> dict[str, object]:
    return query_industry_hierarchy()


@router.get("/features")
def list_features(
    session: DbSession,
    as_of: date | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    min_ttm_revenue_growth_pct: float | None = None,
    min_quarter_revenue_growth_pct: float | None = None,
    max_price_change_12w_pct: float | None = None,
    max_drawdown_52w_pct: float | None = None,
    min_avg_dollar_volume_20d: float | None = None,
    exclude_healthcare: bool = False,
    exclude_sic_prefixes: Annotated[list[str] | None, Query()] = None,
    exclude_industry_groups: Annotated[list[str] | None, Query()] = None,
    nasdaq_nyse_only: bool = True,
    sort_by: str = "avg_dollar_volume_20d",
    descending: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, object]:
    return query_feature_rows(
        session=session,
        as_of_date=as_of.isoformat() if as_of else None,
        min_price=min_price,
        max_price=max_price,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_ttm_revenue_growth_pct=min_ttm_revenue_growth_pct,
        min_quarter_revenue_growth_pct=min_quarter_revenue_growth_pct,
        max_price_change_12w_pct=max_price_change_12w_pct,
        max_drawdown_52w_pct=max_drawdown_52w_pct,
        min_avg_dollar_volume_20d=min_avg_dollar_volume_20d,
        exclude_healthcare=exclude_healthcare,
        exclude_sic_prefixes=exclude_sic_prefixes,
        exclude_industry_groups=exclude_industry_groups,
        nasdaq_nyse_only=nasdaq_nyse_only,
        sort_by=sort_by,
        descending=descending,
        limit=limit,
    )


@router.get("/securities/{ticker}/features")
def get_features_for_ticker(
    ticker: str,
    session: DbSession,
    as_of: date | None = None,
) -> dict[str, object]:
    return query_security_features_for_ticker(
        session, ticker, as_of.isoformat() if as_of else None
    )


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
                "industry_classification": classify_sic(row.sic_code),
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
        select(func.max(DailyPriceBar.trade_date)).where(
            DailyPriceBar.ticker == row.ticker
        )
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
    rows = session.scalars(
        statement.order_by(desc(DailyPriceBar.trade_date)).limit(limit)
    ).all()
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
        return {
            "ticker": security.ticker,
            "cik": None,
            "source": "sec-edgar",
            "items": [],
        }
    statement = select(FinancialFact).where(FinancialFact.cik == security.cik)
    if concept:
        statement = statement.where(FinancialFact.concept == concept)
    if form:
        statement = statement.where(FinancialFact.form == form)
    if filed_after:
        statement = statement.where(FinancialFact.filed_date >= filed_after)
    rows = session.scalars(
        statement.order_by(
            desc(FinancialFact.period_end), desc(FinancialFact.filed_date)
        ).limit(limit)
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
        return {
            "ticker": security.ticker,
            "cik": None,
            "source": "sec-edgar",
            "items": [],
        }
    statement = select(Filing).where(Filing.cik == security.cik)
    if form:
        statement = statement.where(Filing.form == form)
    if filed_after:
        statement = statement.where(Filing.filed_date >= filed_after)
    rows = session.scalars(
        statement.order_by(desc(Filing.filed_date)).limit(limit)
    ).all()
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
