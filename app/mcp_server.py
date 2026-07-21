from typing import Any

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.mcp_queries import (
    get_data_freshness as query_data_freshness,
    get_filings as query_filings,
    get_financial_facts as query_financial_facts,
    get_industry_hierarchy as query_industry_hierarchy,
    get_price_history as query_price_history,
    get_price_revisions as query_price_revisions,
    get_security_history as query_security_history,
    get_security_features as query_features,
    lookup_security as query_security,
    query_security_features as query_features_universe,
    search_securities as query_securities,
)
from app.services.strategy_tracking import (
    get_strategy_run as query_strategy_run,
    list_strategy_runs as query_strategy_runs,
    record_strategy_outcomes as save_strategy_outcomes,
    record_strategy_run as save_strategy_run,
)

settings = get_settings()
mcp = FastMCP(
    "Stock Data Repository",
    instructions=(
        "Authoritative Massive market data and SEC EDGAR source facts remain read-only. "
        "It also exposes versioned deterministic features and neutral user-supplied filtering. "
        "Optional append-oriented tools may store versioned downstream strategy observations, "
        "but this server does not create scores, ranks, recommendations, sizes, or trades. "
        "Treat missing or stale data as unverified and preserve source provenance."
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def search_securities(
    query: str, active_only: bool = True, limit: int = 20
) -> dict[str, Any]:
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
def get_price_revisions(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Return retained provider revisions for historical daily bars."""
    with SessionLocal() as session:
        return query_price_revisions(session, ticker, start_date, end_date, limit)


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
        return query_financial_facts(
            session, ticker, concepts, forms, filed_after, limit
        )


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
def get_industry_hierarchy() -> dict[str, Any]:
    """List every readable SIC division, major group, and curated exclusion group."""
    return query_industry_hierarchy()


@mcp.tool()
def get_security_history(ticker: str, limit: int = 100) -> dict[str, Any]:
    """Return distinct point-in-time reference and SEC metadata snapshots for a ticker."""
    with SessionLocal() as session:
        return query_security_history(session, ticker, limit)


@mcp.tool()
def get_security_features(
    ticker: str,
    as_of_date: str | None = None,
    calculation_version: str | None = None,
) -> dict[str, Any]:
    """Return one ticker's latest deterministic derived fields on or before an optional date."""
    with SessionLocal() as session:
        return query_features(session, ticker, as_of_date, calculation_version)


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
    max_drawdown_12w_high_pct: float | None = None,
    max_drawdown_52w_pct: float | None = None,
    min_avg_dollar_volume_20d: float | None = None,
    exclude_healthcare: bool = False,
    exclude_sic_prefixes: list[str] | None = None,
    exclude_industry_groups: list[str] | None = None,
    nasdaq_nyse_only: bool = True,
    sort_by: str = "avg_dollar_volume_20d",
    descending: bool = True,
    limit: int = 100,
    calculation_version: str | None = None,
) -> dict[str, Any]:
    """Filter deterministic fields and readable SIC-backed industries without scoring or ranking."""
    with SessionLocal() as session:
        return query_features_universe(
            session=session,
            as_of_date=as_of_date,
            min_price=min_price,
            max_price=max_price,
            min_market_cap=min_market_cap,
            max_market_cap=max_market_cap,
            min_ttm_revenue_growth_pct=min_ttm_revenue_growth_pct,
            min_quarter_revenue_growth_pct=min_quarter_revenue_growth_pct,
            max_price_change_12w_pct=max_price_change_12w_pct,
            max_drawdown_12w_high_pct=max_drawdown_12w_high_pct,
            max_drawdown_52w_pct=max_drawdown_52w_pct,
            min_avg_dollar_volume_20d=min_avg_dollar_volume_20d,
            exclude_healthcare=exclude_healthcare,
            exclude_sic_prefixes=exclude_sic_prefixes,
            exclude_industry_groups=exclude_industry_groups,
            nasdaq_nyse_only=nasdaq_nyse_only,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
            calculation_version=calculation_version,
        )


@mcp.tool()
def list_strategy_runs(
    strategy_key: str | None = None,
    strategy_version: str | None = None,
    run_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List versioned as-run, replay, or backtest strategy observations."""
    with SessionLocal() as session:
        return query_strategy_runs(
            session,
            strategy_key,
            strategy_version,
            run_type,
            start_date,
            end_date,
            limit,
        )


@mcp.tool()
def get_strategy_run(run_id: str) -> dict[str, Any]:
    """Return one strategy run with candidates, evidence, and outcome observations."""
    with SessionLocal() as session:
        return query_strategy_run(session, run_id)


if settings.mcp_enable_strategy_writes:

    @mcp.tool()
    def record_strategy_run(
        strategy_key: str,
        strategy_version: str,
        as_of_date: str,
        run_type: str,
        idempotency_key: str,
        configuration: dict[str, Any],
        filters: dict[str, Any],
        candidates: list[dict[str, Any]],
        summary: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        strategy_name: str | None = None,
        skill_fingerprint: str | None = None,
        feature_calculation_version: str | None = None,
        data_cutoff_at_utc: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Append one complete, versioned strategy alert or historical replay."""
        with SessionLocal() as session:
            return save_strategy_run(
                session,
                strategy_key=strategy_key,
                strategy_version=strategy_version,
                as_of_date=as_of_date,
                run_type=run_type,
                idempotency_key=idempotency_key,
                configuration=configuration,
                filters=filters,
                candidates=candidates,
                summary=summary,
                evidence=evidence,
                strategy_name=strategy_name,
                skill_fingerprint=skill_fingerprint,
                feature_calculation_version=feature_calculation_version,
                data_cutoff_at_utc=data_cutoff_at_utc,
                notes=notes,
            )

    @mcp.tool()
    def record_strategy_outcomes(
        run_id: str,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Append idempotent outcome observations for candidates in a stored run."""
        with SessionLocal() as session:
            return save_strategy_outcomes(session, run_id, observations)


def main() -> None:
    configure_logging()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
