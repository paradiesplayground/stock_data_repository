from typing import Any

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.mcp_queries import (
    get_data_freshness as query_data_freshness,
    get_filings as query_filings,
    get_financial_facts as query_financial_facts,
    get_price_history as query_price_history,
    get_security_features as query_features,
    lookup_security as query_security,
    query_security_features as query_features_universe,
    search_securities as query_securities,
)

settings = get_settings()
mcp = FastMCP(
    "Stock Data Repository",
    instructions=(
        "Read-only access to authoritative Massive market data and SEC EDGAR source facts. "
        "It also exposes versioned deterministic features and neutral user-supplied filtering. "
        "This server does not score, rank, recommend, size, or trade securities. "
        "Treat missing or stale data as unverified and preserve source provenance."
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def search_securities(query: str, active_only: bool = True, limit: int = 20) -> dict[str, Any]:
    """Find securities by ticker or company name. This is lookup, not screening or ranking."""
    with SessionLocal() as session:
        return query_securities(session, query, active_only, limit)


@mcp.tool()
def lookup_security(ticker: str) -> dict[str, Any]:
    """Return identifiers, listing metadata, industry metadata, and latest source dates for a ticker."""
    with SessionLocal() as session:
        return query_security(session, ticker)


@mcp.tool()
def get_price_history(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Return chronological provider-adjusted daily OHLCV bars for a ticker and optional date range."""
    with SessionLocal() as session:
        return query_price_history(session, ticker, start_date, end_date, limit)


@mcp.tool()
def get_financial_facts(
    ticker: str,
    concepts: list[str] | None = None,
    forms: list[str] | None = None,
    filed_after: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Return SEC-reported source facts without TTM calculations or competing-tag normalization."""
    with SessionLocal() as session:
        return query_financial_facts(session, ticker, concepts, forms, filed_after, limit)


@mcp.tool()
def get_filings(
    ticker: str,
    forms: list[str] | None = None,
    filed_after: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return SEC filing metadata and canonical document URLs for a ticker."""
    with SessionLocal() as session:
        return query_filings(session, ticker, forms, filed_after, limit)


@mcp.tool()
def get_data_freshness() -> dict[str, Any]:
    """Return latest source dates, job outcomes, errors, and durable ingestion checkpoints."""
    with SessionLocal() as session:
        return query_data_freshness(session)


@mcp.tool()
def get_security_features(ticker: str, as_of_date: str | None = None) -> dict[str, Any]:
    """Return one ticker's latest deterministic derived fields on or before an optional date."""
    with SessionLocal() as session:
        return query_features(session, ticker, as_of_date)


@mcp.tool()
def query_security_features(
    as_of_date: str | None = None,
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
    nasdaq_nyse_only: bool = True,
    sort_by: str = "avg_dollar_volume_20d",
    descending: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    """Filter deterministic fields using caller-provided thresholds without scoring or ranking."""
    with SessionLocal() as session:
        return query_features_universe(
            session,
            as_of_date,
            min_price,
            max_price,
            min_market_cap,
            max_market_cap,
            min_ttm_revenue_growth_pct,
            min_quarter_revenue_growth_pct,
            max_price_change_12w_pct,
            max_drawdown_52w_pct,
            min_avg_dollar_volume_20d,
            exclude_healthcare,
            nasdaq_nyse_only,
            sort_by,
            descending,
            limit,
        )


def main() -> None:
    configure_logging()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
