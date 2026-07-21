from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.models import DailyPriceBar, FinancialFact, Security, SecurityDailyFeature
from app.services.feature_calculation import (
    CALCULATION_VERSION,
    FEATURE_FACT_CONCEPTS,
    _financial_metrics,
    _price_metrics,
)

VALIDATED_FIELDS = (
    "price_date",
    "close",
    "price_change_12w_pct",
    "drawdown_52w_pct",
    "avg_volume_20d",
    "avg_dollar_volume_20d",
    "ema_10",
    "ema_20",
    "rsi_14",
    "relative_volume_20d",
    "revenue_ttm",
    "revenue_ttm_yoy_pct",
    "latest_quarter_revenue",
    "latest_quarter_revenue_yoy_pct",
    "revenue_concept",
    "gross_profit_ttm",
    "gross_margin_ttm_pct",
    "cash_and_short_term_investments",
    "total_debt",
    "current_ratio",
    "operating_cash_flow_ttm",
    "capital_expenditures_ttm",
    "free_cash_flow_ttm",
    "cash_runway_months",
    "shares_outstanding",
    "share_count_yoy_pct",
    "approximate_market_cap",
    "latest_financial_period_end",
    "latest_source_filing_date",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, Decimal)):
        return str(value)
    return value


def _values_match(stored: Any, recomputed: Any) -> bool:
    if stored is None or recomputed is None:
        return stored is recomputed
    if isinstance(stored, Decimal) and isinstance(recomputed, Decimal):
        tolerance = max(abs(recomputed) * Decimal("0.00000001"), Decimal("0.00000001"))
        return abs(stored - recomputed) <= tolerance
    return stored == recomputed


def validate_feature_calculations(
    session: Session,
    tickers: list[str],
    as_of_date: date | None = None,
) -> dict[str, Any]:
    normalized_tickers = list(dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker.strip()))
    if not normalized_tickers:
        raise ValueError("at least one ticker is required")
    if len(normalized_tickers) > 25:
        raise ValueError("at most 25 tickers may be validated at once")

    results: list[dict[str, Any]] = []
    for ticker in normalized_tickers:
        security = session.get(Security, ticker)
        if security is None:
            results.append({"ticker": ticker, "status": "security_not_found"})
            continue
        feature_statement = select(SecurityDailyFeature).where(
            SecurityDailyFeature.ticker == ticker
        )
        if as_of_date:
            feature_statement = feature_statement.where(
                SecurityDailyFeature.as_of_date <= as_of_date
            )
        feature = session.scalar(
            feature_statement.order_by(desc(SecurityDailyFeature.as_of_date)).limit(1)
        )
        if feature is None:
            results.append({"ticker": ticker, "status": "feature_snapshot_not_found"})
            continue

        effective_date = feature.as_of_date
        price_rows = session.scalars(
            select(DailyPriceBar)
            .where(
                DailyPriceBar.ticker == ticker,
                DailyPriceBar.trade_date.between(
                    effective_date - timedelta(days=400), effective_date
                ),
            )
            .order_by(DailyPriceBar.trade_date)
        ).all()
        if not price_rows:
            results.append({"ticker": ticker, "status": "price_history_not_found"})
            continue

        fact_rows: list[FinancialFact] = []
        if security.cik:
            fact_rows = session.scalars(
                select(FinancialFact).where(
                    FinancialFact.cik == security.cik,
                    FinancialFact.concept.in_(FEATURE_FACT_CONCEPTS),
                    FinancialFact.period_end.between(
                        effective_date - timedelta(days=900), effective_date
                    ),
                    or_(
                        FinancialFact.filed_date.is_(None),
                        FinancialFact.filed_date <= effective_date,
                    ),
                )
            ).all()
        facts_by_concept: dict[str, list[FinancialFact]] = defaultdict(list)
        for fact in fact_rows:
            facts_by_concept[fact.concept].append(fact)

        price_metrics, _ = _price_metrics(price_rows, effective_date)
        financial_metrics, _ = _financial_metrics(
            facts_by_concept, effective_date, price_metrics["close"]
        )
        recomputed = {**price_metrics, **financial_metrics}
        comparisons = {
            field: {
                "stored": _json_value(getattr(feature, field)),
                "recomputed": _json_value(recomputed[field]),
                "matches": _values_match(getattr(feature, field), recomputed[field]),
            }
            for field in VALIDATED_FIELDS
        }
        mismatches = [field for field, result in comparisons.items() if not result["matches"]]
        results.append(
            {
                "ticker": ticker,
                "status": "matches" if not mismatches else "mismatch",
                "as_of_date": effective_date.isoformat(),
                "stored_calculation_version": feature.calculation_version,
                "current_calculation_version": CALCULATION_VERSION,
                "source_rows": {"prices": len(price_rows), "financial_facts": len(fact_rows)},
                "mismatched_fields": mismatches,
                "comparisons": comparisons,
            }
        )

    return {
        "validation": "stored derived fields recomputed from local Massive and SEC source rows",
        "external_source_comparison": False,
        "count": len(results),
        "all_match": bool(results) and all(item.get("status") == "matches" for item in results),
        "items": results,
    }
