import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import DailyPriceBar, FinancialFact, Security, SecurityDailyFeature
from app.services.massive_ingestion import local_today, market_target_date
from app.services.runs import RunTracker

logger = logging.getLogger(__name__)

CALCULATION_VERSION = "1.0.1"
HUNDRED = Decimal("100")

REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
GROSS_PROFIT_CONCEPTS = ("GrossProfit",)
OPERATING_CASH_FLOW_CONCEPTS = ("NetCashProvidedByUsedInOperatingActivities",)
CAPEX_CONCEPTS = ("PaymentsToAcquirePropertyPlantAndEquipment",)
CASH_CONCEPTS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
SHORT_TERM_INVESTMENT_CONCEPTS = ("ShortTermInvestments",)
CURRENT_ASSET_CONCEPTS = ("AssetsCurrent",)
CURRENT_LIABILITY_CONCEPTS = ("LiabilitiesCurrent",)
DEBT_TOTAL_CONCEPTS = ("LongTermDebt",)
DEBT_CURRENT_CONCEPTS = ("LongTermDebtCurrent",)
DEBT_NONCURRENT_CONCEPTS = ("LongTermDebtNoncurrent",)
SHARE_CONCEPTS = (
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
)

FEATURE_FACT_CONCEPTS = set(
    REVENUE_CONCEPTS
    + GROSS_PROFIT_CONCEPTS
    + OPERATING_CASH_FLOW_CONCEPTS
    + CAPEX_CONCEPTS
    + CASH_CONCEPTS
    + SHORT_TERM_INVESTMENT_CONCEPTS
    + CURRENT_ASSET_CONCEPTS
    + CURRENT_LIABILITY_CONCEPTS
    + DEBT_TOTAL_CONCEPTS
    + DEBT_CURRENT_CONCEPTS
    + DEBT_NONCURRENT_CONCEPTS
    + SHARE_CONCEPTS
)


def _percent_change(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    if current is None or prior in (None, Decimal("0")):
        return None
    return ((current / prior) - Decimal("1")) * HUNDRED


def _ema(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    multiplier = Decimal("2") / Decimal(period + 1)
    result = sum(values[:period], Decimal("0")) / Decimal(period)
    for value in values[period:]:
        result = ((value - result) * multiplier) + result
    return result


def _rsi(values: list[Decimal], period: int = 14) -> Decimal | None:
    if len(values) <= period:
        return None
    changes = [current - prior for prior, current in zip(values, values[1:])]
    gains = [max(change, Decimal("0")) for change in changes]
    losses = [max(-change, Decimal("0")) for change in changes]
    average_gain = sum(gains[:period], Decimal("0")) / Decimal(period)
    average_loss = sum(losses[:period], Decimal("0")) / Decimal(period)
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * Decimal(period - 1)) + gain) / Decimal(period)
        average_loss = ((average_loss * Decimal(period - 1)) + loss) / Decimal(period)
    if average_loss == 0:
        return HUNDRED if average_gain > 0 else Decimal("50")
    relative_strength = average_gain / average_loss
    return HUNDRED - (HUNDRED / (Decimal("1") + relative_strength))


def _price_metrics(rows: list[DailyPriceBar], as_of_date: date) -> tuple[dict[str, Any], list[str]]:
    eligible = [row for row in rows if row.trade_date <= as_of_date]
    if not eligible:
        raise ValueError("price history is empty")
    eligible.sort(key=lambda row: row.trade_date)
    latest = eligible[-1]
    flags: list[str] = []
    if latest.trade_date < as_of_date - timedelta(days=5):
        flags.append("stale_price")

    target_12w = as_of_date - timedelta(weeks=12)
    prior_12w = next((row for row in reversed(eligible) if row.trade_date <= target_12w), None)
    change_12w = _percent_change(latest.close, prior_12w.close if prior_12w else None)
    if change_12w is None:
        flags.append("insufficient_12w_history")

    year_rows = [row for row in eligible if row.trade_date >= as_of_date - timedelta(days=365)]
    high_52w = max((row.high for row in year_rows), default=None)
    drawdown_52w = _percent_change(latest.close, high_52w)
    if len(year_rows) < 200:
        flags.append("partial_52w_history")

    last_20 = eligible[-20:]
    avg_volume = (
        sum((row.volume for row in last_20), Decimal("0")) / Decimal(len(last_20))
        if last_20
        else None
    )
    avg_dollar_volume = (
        sum((row.volume * row.close for row in last_20), Decimal("0"))
        / Decimal(len(last_20))
        if last_20
        else None
    )
    previous_20 = eligible[-21:-1]
    prior_avg_volume = (
        sum((row.volume for row in previous_20), Decimal("0")) / Decimal(len(previous_20))
        if previous_20
        else None
    )
    relative_volume = (
        latest.volume / prior_avg_volume if prior_avg_volume not in (None, Decimal("0")) else None
    )
    closes = [row.close for row in eligible]
    return (
        {
            "price_date": latest.trade_date,
            "close": latest.close,
            "price_change_12w_pct": change_12w,
            "drawdown_52w_pct": drawdown_52w,
            "avg_volume_20d": avg_volume,
            "avg_dollar_volume_20d": avg_dollar_volume,
            "ema_10": _ema(closes, 10),
            "ema_20": _ema(closes, 20),
            "rsi_14": _rsi(closes, 14),
            "relative_volume_20d": relative_volume,
        },
        flags,
    )


def _duration_days(fact: FinancialFact) -> int | None:
    if fact.period_start is None:
        return None
    return (fact.period_end - fact.period_start).days + 1


def _eligible_facts(
    facts: Iterable[FinancialFact],
    period_cutoff: date,
    filing_cutoff: date,
) -> list[FinancialFact]:
    return [
        fact
        for fact in facts
        if fact.period_end <= period_cutoff
        and (fact.filed_date is None or fact.filed_date <= filing_cutoff)
    ]


def _latest_by_period(facts: Iterable[FinancialFact]) -> list[FinancialFact]:
    selected: dict[tuple[date | None, date], FinancialFact] = {}
    for fact in facts:
        # Later filings commonly repeat comparative periods under the new
        # filing's fiscal-year metadata. Period dates are the stable identity.
        key = (fact.period_start, fact.period_end)
        existing = selected.get(key)
        existing_filed = existing.filed_date if existing and existing.filed_date else date.min
        fact_filed = fact.filed_date or date.min
        if existing is None or (fact_filed, fact.accession_number or "") >= (
            existing_filed,
            existing.accession_number or "",
        ):
            selected[key] = fact
    return list(selected.values())


def _ttm_value(
    facts: Iterable[FinancialFact],
    period_cutoff: date,
    filing_cutoff: date | None = None,
) -> tuple[Decimal | None, date | None, str]:
    filing_cutoff = filing_cutoff or period_cutoff
    eligible = _latest_by_period(_eligible_facts(facts, period_cutoff, filing_cutoff))
    annual = [fact for fact in eligible if (_duration_days(fact) or 0) in range(300, 401)]
    if not annual:
        return None, None, "missing_annual"
    latest_annual = max(annual, key=lambda fact: (fact.period_end, fact.filed_date or date.min))
    current_interims = [
        fact
        for fact in eligible
        if fact.period_end > latest_annual.period_end
        and (_duration_days(fact) or 0) in range(60, 301)
    ]
    if not current_interims:
        return latest_annual.value, latest_annual.period_end, "annual_only"
    current = max(current_interims, key=lambda fact: (fact.period_end, _duration_days(fact) or 0))
    current_duration = _duration_days(current) or 0
    prior_target = current.period_end - timedelta(days=365)
    prior_interims = [
        fact
        for fact in eligible
        if abs((fact.period_end - prior_target).days) <= 45
        and abs((_duration_days(fact) or 0) - current_duration) <= 35
        and fact.period_end <= latest_annual.period_end
    ]
    if not prior_interims:
        return None, current.period_end, "missing_comparable_interim"
    prior = min(
        prior_interims,
        key=lambda fact: (
            abs((fact.period_end - prior_target).days),
            abs((_duration_days(fact) or 0) - current_duration),
        ),
    )
    return latest_annual.value + current.value - prior.value, current.period_end, "annual_plus_ytd"


def _latest_quarter_pair(
    facts: Iterable[FinancialFact], as_of_date: date
) -> tuple[Decimal | None, Decimal | None, date | None]:
    eligible = _latest_by_period(_eligible_facts(facts, as_of_date, as_of_date))
    quarters = [fact for fact in eligible if (_duration_days(fact) or 0) in range(65, 121)]
    if not quarters:
        return None, None, None
    current = max(quarters, key=lambda fact: (fact.period_end, fact.filed_date or date.min))
    target = current.period_end - timedelta(days=365)
    comparable = [
        fact
        for fact in quarters
        if abs((fact.period_end - target).days) <= 45
        and abs((_duration_days(fact) or 0) - (_duration_days(current) or 0)) <= 20
    ]
    if not comparable:
        return current.value, None, current.period_end
    prior = min(comparable, key=lambda fact: abs((fact.period_end - target).days))
    return current.value, prior.value, current.period_end


def _select_revenue_concept(
    facts_by_concept: dict[str, list[FinancialFact]], as_of_date: date
) -> tuple[str | None, list[FinancialFact]]:
    best: tuple[tuple[int, int, date, int], str, list[FinancialFact]] | None = None
    for priority, concept in enumerate(REVENUE_CONCEPTS):
        facts = facts_by_concept.get(concept, [])
        current_ttm, ttm_end, _ = _ttm_value(facts, as_of_date)
        quarter, prior_quarter, quarter_end = _latest_quarter_pair(facts, as_of_date)
        score = (
            int(current_ttm is not None),
            int(quarter is not None and prior_quarter is not None),
            max(ttm_end or date.min, quarter_end or date.min),
            -priority,
        )
        if facts and (best is None or score > best[0]):
            best = (score, concept, facts)
    return (best[1], best[2]) if best else (None, [])


def _latest_instant(
    facts_by_concept: dict[str, list[FinancialFact]],
    concepts: tuple[str, ...],
    as_of_date: date,
) -> tuple[Decimal | None, date | None]:
    for concept in concepts:
        candidates = _eligible_facts(facts_by_concept.get(concept, []), as_of_date, as_of_date)
        instant = [fact for fact in candidates if _duration_days(fact) in (None, 1)]
        if instant:
            latest = max(instant, key=lambda fact: (fact.period_end, fact.filed_date or date.min))
            return latest.value, latest.period_end
    return None, None


def _flow_value(
    facts_by_concept: dict[str, list[FinancialFact]],
    concepts: tuple[str, ...],
    as_of_date: date,
) -> tuple[Decimal | None, date | None, str]:
    for concept in concepts:
        value, period_end, status = _ttm_value(facts_by_concept.get(concept, []), as_of_date)
        if value is not None:
            return value, period_end, status
    return None, None, "unavailable"


def _shares_metrics(
    facts_by_concept: dict[str, list[FinancialFact]], as_of_date: date
) -> tuple[Decimal | None, Decimal | None, date | None]:
    for concept in SHARE_CONCEPTS:
        candidates = _eligible_facts(facts_by_concept.get(concept, []), as_of_date, as_of_date)
        candidates = [fact for fact in candidates if "share" in fact.unit.lower()]
        if not candidates:
            continue
        latest = max(candidates, key=lambda fact: (fact.period_end, fact.filed_date or date.min))
        target = latest.period_end - timedelta(days=365)
        priors = [
            fact
            for fact in candidates
            if abs((fact.period_end - target).days) <= 90 and fact.period_end < latest.period_end
        ]
        prior = min(priors, key=lambda fact: abs((fact.period_end - target).days)) if priors else None
        return latest.value, _percent_change(latest.value, prior.value if prior else None), latest.period_end
    return None, None, None


def _financial_metrics(
    facts_by_concept: dict[str, list[FinancialFact]],
    as_of_date: date,
    close: Decimal,
) -> tuple[dict[str, Any], list[str]]:
    flags: list[str] = []
    concept, revenue_facts = _select_revenue_concept(facts_by_concept, as_of_date)
    revenue_ttm, revenue_end, revenue_status = _ttm_value(revenue_facts, as_of_date)
    prior_revenue_ttm, _, _ = _ttm_value(
        revenue_facts,
        as_of_date - timedelta(days=365),
        filing_cutoff=as_of_date,
    )
    quarter_revenue, prior_quarter_revenue, quarter_end = _latest_quarter_pair(
        revenue_facts, as_of_date
    )
    if revenue_ttm is None:
        flags.append("revenue_ttm_unavailable")
    elif revenue_status == "annual_only":
        flags.append("revenue_ttm_annual_only")
    if prior_revenue_ttm is None:
        flags.append("revenue_ttm_growth_unavailable")
    if prior_quarter_revenue is None:
        flags.append("quarterly_revenue_growth_unavailable")

    gross_profit, gross_end, _ = _flow_value(
        facts_by_concept, GROSS_PROFIT_CONCEPTS, as_of_date
    )
    gross_margin = (
        (gross_profit / revenue_ttm) * HUNDRED
        if gross_profit is not None and revenue_ttm not in (None, Decimal("0"))
        else None
    )
    cash, cash_end = _latest_instant(facts_by_concept, CASH_CONCEPTS, as_of_date)
    investments, investments_end = _latest_instant(
        facts_by_concept, SHORT_TERM_INVESTMENT_CONCEPTS, as_of_date
    )
    if cash is not None and investments is not None and cash_end and investments_end:
        if abs((cash_end - investments_end).days) <= 120:
            cash += investments
    current_assets, assets_end = _latest_instant(
        facts_by_concept, CURRENT_ASSET_CONCEPTS, as_of_date
    )
    current_liabilities, liabilities_end = _latest_instant(
        facts_by_concept, CURRENT_LIABILITY_CONCEPTS, as_of_date
    )
    current_ratio = (
        current_assets / current_liabilities
        if current_assets is not None and current_liabilities not in (None, Decimal("0"))
        else None
    )
    debt_current, debt_current_end = _latest_instant(
        facts_by_concept, DEBT_CURRENT_CONCEPTS, as_of_date
    )
    debt_noncurrent, debt_noncurrent_end = _latest_instant(
        facts_by_concept, DEBT_NONCURRENT_CONCEPTS, as_of_date
    )
    debt_total, debt_total_end = _latest_instant(
        facts_by_concept, DEBT_TOTAL_CONCEPTS, as_of_date
    )
    if debt_current is not None and debt_noncurrent is not None:
        total_debt = (debt_current or Decimal("0")) + (debt_noncurrent or Decimal("0"))
    elif debt_total is not None:
        total_debt = debt_total
    else:
        total_debt = debt_current if debt_current is not None else debt_noncurrent

    operating_cash_flow, ocf_end, ocf_status = _flow_value(
        facts_by_concept, OPERATING_CASH_FLOW_CONCEPTS, as_of_date
    )
    capex, capex_end, capex_status = _flow_value(facts_by_concept, CAPEX_CONCEPTS, as_of_date)
    free_cash_flow = (
        operating_cash_flow - capex
        if operating_cash_flow is not None and capex is not None
        else None
    )
    cash_runway = (
        (cash / -free_cash_flow) * Decimal("12")
        if cash is not None and free_cash_flow is not None and free_cash_flow < 0
        else None
    )
    if operating_cash_flow is None or capex is None:
        flags.append("free_cash_flow_unavailable")
    elif ocf_status == "annual_only" or capex_status == "annual_only":
        flags.append("free_cash_flow_annual_only")
    if cash is None:
        flags.append("cash_unavailable")
    if current_ratio is None:
        flags.append("current_ratio_unavailable")

    shares, share_growth, shares_end = _shares_metrics(facts_by_concept, as_of_date)
    market_cap = shares * close if shares is not None else None
    if market_cap is None:
        flags.append("market_cap_unavailable")

    all_facts = [fact for facts in facts_by_concept.values() for fact in facts]
    eligible_all = _eligible_facts(all_facts, as_of_date, as_of_date)
    latest_filing = max(
        (fact.filed_date for fact in eligible_all if fact.filed_date is not None),
        default=None,
    )
    period_candidates = [
        revenue_end,
        quarter_end,
        gross_end,
        cash_end,
        investments_end,
        assets_end,
        liabilities_end,
        debt_current_end,
        debt_noncurrent_end,
        debt_total_end,
        ocf_end,
        capex_end,
        shares_end,
    ]
    latest_period = max((value for value in period_candidates if value is not None), default=None)
    if latest_period and latest_period < as_of_date - timedelta(days=190):
        flags.append("stale_financial_period")

    return (
        {
            "revenue_ttm": revenue_ttm,
            "revenue_ttm_yoy_pct": _percent_change(revenue_ttm, prior_revenue_ttm),
            "latest_quarter_revenue": quarter_revenue,
            "latest_quarter_revenue_yoy_pct": _percent_change(
                quarter_revenue, prior_quarter_revenue
            ),
            "revenue_concept": concept,
            "gross_profit_ttm": gross_profit,
            "gross_margin_ttm_pct": gross_margin,
            "cash_and_short_term_investments": cash,
            "total_debt": total_debt,
            "current_ratio": current_ratio,
            "operating_cash_flow_ttm": operating_cash_flow,
            "capital_expenditures_ttm": capex,
            "free_cash_flow_ttm": free_cash_flow,
            "cash_runway_months": cash_runway,
            "shares_outstanding": shares,
            "share_count_yoy_pct": share_growth,
            "approximate_market_cap": market_cap,
            "latest_financial_period_end": latest_period,
            "latest_source_filing_date": latest_filing,
        },
        flags,
    )


def calculate_daily_features(
    session: Session,
    settings: Settings,
    as_of_date: date | None = None,
) -> tuple[int, int]:
    tracker = RunTracker(session, "derived_features", "massive+sec-edgar")
    seen = written = 0
    try:
        latest_trade_date = session.scalar(select(func.max(DailyPriceBar.trade_date)))
        if latest_trade_date is None:
            raise RuntimeError("Cannot calculate features before market data is loaded")
        expected_date = market_target_date(
            local_today(settings), settings.massive_market_lag_days
        )
        effective_date = as_of_date or expected_date
        if as_of_date is None and latest_trade_date < expected_date:
            raise RuntimeError(
                f"Market data is stale: latest={latest_trade_date}, expected={expected_date}"
            )
        source_rows = session.scalar(
            select(func.count(DailyPriceBar.id)).where(
                DailyPriceBar.trade_date == effective_date
            )
        ) or 0
        if source_rows < settings.massive_min_daily_results:
            raise RuntimeError(
                f"Market data is incomplete for {effective_date}: {source_rows} rows; "
                f"expected at least {settings.massive_min_daily_results}"
            )

        securities = session.scalars(
            select(Security).where(
                Security.active.is_(True),
                Security.market == "stocks",
                Security.locale == "us",
                Security.security_type == "CS",
            )
        ).all()
        seen = len(securities)
        tickers = {security.ticker for security in securities}
        ciks = {security.cik for security in securities if security.cik}
        cik_ticker_counts: dict[str, int] = defaultdict(int)
        for security in securities:
            if security.cik:
                cik_ticker_counts[security.cik] += 1
        history_start = effective_date - timedelta(days=400)

        price_rows = session.scalars(
            select(DailyPriceBar)
            .where(
                DailyPriceBar.ticker.in_(tickers),
                DailyPriceBar.trade_date.between(history_start, effective_date),
            )
            .order_by(DailyPriceBar.ticker, DailyPriceBar.trade_date)
        ).all()
        prices_by_ticker: dict[str, list[DailyPriceBar]] = defaultdict(list)
        for row in price_rows:
            prices_by_ticker[row.ticker].append(row)

        fact_rows = session.scalars(
            select(FinancialFact).where(
                FinancialFact.cik.in_(ciks),
                FinancialFact.concept.in_(FEATURE_FACT_CONCEPTS),
                FinancialFact.period_end >= effective_date - timedelta(days=900),
                FinancialFact.period_end <= effective_date,
                or_(
                    FinancialFact.filed_date.is_(None),
                    FinancialFact.filed_date <= effective_date,
                ),
            )
        ).all()
        facts_by_cik: dict[str, dict[str, list[FinancialFact]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for fact in fact_rows:
            facts_by_cik[fact.cik][fact.concept].append(fact)

        output_rows: list[dict[str, Any]] = []
        for position, security in enumerate(securities, start=1):
            price_history = prices_by_ticker.get(security.ticker, [])
            if not price_history:
                continue
            price_metrics, price_flags = _price_metrics(price_history, effective_date)
            financial_metrics, financial_flags = _financial_metrics(
                facts_by_cik.get(security.cik or "", {}),
                effective_date,
                price_metrics["close"],
            )
            metadata_flags = []
            if not security.cik:
                metadata_flags.append("missing_cik")
            if not security.sic_code:
                metadata_flags.append("missing_sic")
            if security.cik and cik_ticker_counts[security.cik] > 1:
                metadata_flags.append("shared_cik_multiple_tickers")
            flags = sorted(set(price_flags + financial_flags + metadata_flags))
            output_rows.append(
                {
                    "ticker": security.ticker,
                    "as_of_date": effective_date,
                    **price_metrics,
                    **financial_metrics,
                    "calculation_version": CALCULATION_VERSION,
                    "quality_flags": flags,
                }
            )
            if position % 1000 == 0:
                logger.info("Calculated features for %s/%s securities", position, len(securities))

        for start in range(0, len(output_rows), 500):
            batch = output_rows[start : start + 500]
            statement = insert(SecurityDailyFeature).values(batch)
            excluded = statement.excluded
            update_values = {
                column.name: getattr(excluded, column.name)
                for column in SecurityDailyFeature.__table__.columns
                if column.name not in {"id", "ticker", "as_of_date", "calculated_at_utc"}
            }
            update_values["calculated_at_utc"] = func.now()
            statement = statement.on_conflict_do_update(
                constraint="uq_security_features_ticker_date",
                set_=update_values,
            )
            session.execute(statement)
            session.commit()
            written += len(batch)

        tracker.succeed(
            seen,
            written,
            {
                "as_of_date": effective_date.isoformat(),
                "expected_market_date": expected_date.isoformat(),
                "source_market_rows": source_rows,
                "calculation_version": CALCULATION_VERSION,
                "price_rows_read": len(price_rows),
                "financial_facts_read": len(fact_rows),
            },
        )
        return seen, written
    except Exception as error:
        tracker.fail(error, seen, written)
        raise
