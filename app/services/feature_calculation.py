import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    DailyPriceBar,
    FinancialFact,
    Security,
    SecurityDailyFeature,
    SecurityReferenceHistory,
)
from app.services.massive_ingestion import local_today, market_target_date
from app.services.runs import RunTracker

logger = logging.getLogger(__name__)

CALCULATION_VERSION = "1.4.0"
HUNDRED = Decimal("100")

REFERENCE_FIELDS = (
    "name",
    "market",
    "locale",
    "currency",
    "primary_exchange",
    "security_type",
    "active",
    "cik",
    "composite_figi",
    "share_class_figi",
    "sic_code",
    "sic_description",
    "fiscal_year_end",
    "state_of_incorporation",
)


@dataclass(frozen=True)
class FeatureSecurity:
    ticker: str
    name: str | None
    market: str | None
    locale: str | None
    currency: str | None
    primary_exchange: str | None
    security_type: str | None
    active: bool
    current_active: bool
    cik: str | None
    composite_figi: str | None
    share_class_figi: str | None
    sic_code: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    state_of_incorporation: str | None
    reference_metadata_imputed: bool
    reference_observed_at_utc: datetime | None

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
SHORT_TERM_INVESTMENT_CONCEPTS = (
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
)
SHORT_TERM_INVESTMENT_COMPONENT_CONCEPTS = (
    "MarketableDebtSecuritiesCurrent",
    "MarketableEquitySecuritiesCurrent",
)
CURRENT_ASSET_CONCEPTS = ("AssetsCurrent",)
CURRENT_LIABILITY_CONCEPTS = ("LiabilitiesCurrent",)
DEBT_TOTAL_CONCEPTS = ("LongTermDebt",)
DEBT_CURRENT_CONCEPTS = ("LongTermDebtCurrent",)
DEBT_NONCURRENT_CONCEPTS = ("LongTermDebtNoncurrent",)
SHORT_TERM_DEBT_CONCEPTS = ("ShortTermDebtCurrent", "ShortTermBorrowings")
COMMERCIAL_PAPER_CONCEPTS = ("CommercialPaper",)
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
    + SHORT_TERM_INVESTMENT_COMPONENT_CONCEPTS
    + CURRENT_ASSET_CONCEPTS
    + CURRENT_LIABILITY_CONCEPTS
    + DEBT_TOTAL_CONCEPTS
    + DEBT_CURRENT_CONCEPTS
    + DEBT_NONCURRENT_CONCEPTS
    + SHORT_TERM_DEBT_CONCEPTS
    + COMMERCIAL_PAPER_CONCEPTS
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


def _atr(rows: list[DailyPriceBar], period: int = 14) -> Decimal | None:
    if len(rows) <= period:
        return None
    ranges: list[Decimal] = []
    for previous, current in zip(rows, rows[1:]):
        current_low = getattr(current, "low", current.close)
        current_high = getattr(current, "high", current.close)
        ranges.append(
            max(
                current_high - current_low,
                abs(current_high - previous.close),
                abs(current_low - previous.close),
            )
        )
    return sum(ranges[-period:], Decimal("0")) / Decimal(period)


def _price_metrics(
    rows: list[DailyPriceBar],
    as_of_date: date,
    benchmark_20d_return: Decimal | None = None,
) -> tuple[dict[str, Any], list[str]]:
    eligible = [row for row in rows if row.trade_date <= as_of_date]
    if not eligible:
        raise ValueError("price history is empty")
    eligible.sort(key=lambda row: row.trade_date)
    latest = eligible[-1]
    flags: list[str] = []
    if latest.trade_date < as_of_date - timedelta(days=5):
        flags.append("stale_price")

    target_12w = as_of_date - timedelta(weeks=12)
    prior_12w = next(
        (row for row in reversed(eligible) if row.trade_date <= target_12w), None
    )
    change_12w = _percent_change(latest.close, prior_12w.close if prior_12w else None)
    if change_12w is None:
        flags.append("insufficient_12w_history")

    twelve_week_rows = [row for row in eligible if row.trade_date >= target_12w]
    high_12w = max((row.high for row in twelve_week_rows), default=None)
    drawdown_12w_high = _percent_change(latest.close, high_12w)

    year_rows = [
        row for row in eligible if row.trade_date >= as_of_date - timedelta(days=365)
    ]
    high_52w = max((row.high for row in year_rows), default=None)
    drawdown_52w = _percent_change(latest.close, high_52w)
    if len(year_rows) < 200:
        flags.append("partial_52w_history")

    last_20 = eligible[-20:]
    last_60 = eligible[-60:]
    prior_20_session = eligible[-21] if len(eligible) >= 21 else None
    change_20d = _percent_change(
        latest.close, prior_20_session.close if prior_20_session else None
    )
    if change_20d is None:
        flags.append("insufficient_20d_history")
    high_20d = max((row.high for row in last_20), default=None)
    low_20d = min((getattr(row, "low", row.close) for row in last_20), default=None)
    high_60d = max((row.high for row in last_60), default=None)
    low_60d = min((getattr(row, "low", row.close) for row in last_60), default=None)
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
        sum((row.volume for row in previous_20), Decimal("0"))
        / Decimal(len(previous_20))
        if previous_20
        else None
    )
    relative_volume = (
        latest.volume / prior_avg_volume
        if prior_avg_volume not in (None, Decimal("0"))
        else None
    )
    atr_14 = _atr(eligible, 14)
    if atr_14 is None:
        flags.append("insufficient_atr_history")
    previous_close = eligible[-2].close if len(eligible) >= 2 else None
    latest_open = getattr(latest, "open", None)
    closes = [row.close for row in eligible]
    return (
        {
            "price_date": latest.trade_date,
            "close": latest.close,
            "daily_return_pct": _percent_change(latest.close, previous_close),
            "price_change_20d_pct": change_20d,
            "price_change_12w_pct": change_12w,
            "drawdown_12w_high_pct": drawdown_12w_high,
            "drawdown_52w_pct": drawdown_52w,
            "high_20d": high_20d,
            "low_20d": low_20d,
            "high_60d": high_60d,
            "low_60d": low_60d,
            "distance_to_20d_high_pct": _percent_change(latest.close, high_20d),
            "distance_to_60d_high_pct": _percent_change(latest.close, high_60d),
            "atr_14": atr_14,
            "atr_14_pct": (
                (atr_14 / latest.close) * HUNDRED
                if atr_14 is not None and latest.close != 0
                else None
            ),
            "overnight_gap_pct": _percent_change(latest_open, previous_close),
            "relative_return_20d_vs_qqq_pct": (
                change_20d - benchmark_20d_return
                if change_20d is not None and benchmark_20d_return is not None
                else None
            ),
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
    eligible: list[FinancialFact] = []
    for fact in facts:
        available_at = getattr(fact, "available_at_utc", None)
        available_date = available_at.date() if available_at else fact.filed_date
        if fact.period_end <= period_cutoff and (
            available_date is None or available_date <= filing_cutoff
        ):
            eligible.append(fact)
    return eligible


def _latest_by_period(facts: Iterable[FinancialFact]) -> list[FinancialFact]:
    selected: dict[tuple[date | None, date], FinancialFact] = {}
    for fact in facts:
        # Later filings commonly repeat comparative periods under the new
        # filing's fiscal-year metadata. Period dates are the stable identity.
        key = (fact.period_start, fact.period_end)
        existing = selected.get(key)
        existing_filed = (
            existing.filed_date if existing and existing.filed_date else date.min
        )
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
    annual = [
        fact for fact in eligible if (_duration_days(fact) or 0) in range(300, 401)
    ]
    if not annual:
        return None, None, "missing_annual"
    latest_annual = max(
        annual, key=lambda fact: (fact.period_end, fact.filed_date or date.min)
    )
    current_interims = [
        fact
        for fact in eligible
        if fact.period_end > latest_annual.period_end
        and (_duration_days(fact) or 0) in range(60, 301)
    ]
    if not current_interims:
        return latest_annual.value, latest_annual.period_end, "annual_only"
    current = max(
        current_interims, key=lambda fact: (fact.period_end, _duration_days(fact) or 0)
    )
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
    return (
        latest_annual.value + current.value - prior.value,
        current.period_end,
        "annual_plus_ytd",
    )


def _latest_quarter_pair(
    facts: Iterable[FinancialFact], as_of_date: date
) -> tuple[Decimal | None, Decimal | None, date | None]:
    eligible = _latest_by_period(_eligible_facts(facts, as_of_date, as_of_date))
    quarters = [
        fact for fact in eligible if (_duration_days(fact) or 0) in range(65, 121)
    ]
    if not quarters:
        return None, None, None
    current = max(
        quarters, key=lambda fact: (fact.period_end, fact.filed_date or date.min)
    )
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
    best: tuple[tuple[date, date, int, str], FinancialFact] | None = None
    for priority, concept in enumerate(concepts):
        candidates = _eligible_facts(
            facts_by_concept.get(concept, []), as_of_date, as_of_date
        )
        instant = [fact for fact in candidates if _duration_days(fact) in (None, 1)]
        if instant:
            latest = max(
                instant, key=lambda fact: (fact.period_end, fact.filed_date or date.min)
            )
            score = (
                latest.period_end,
                latest.filed_date or date.min,
                -priority,
                latest.accession_number or "",
            )
            if best is None or score > best[0]:
                best = (score, latest)
    return (best[1].value, best[1].period_end) if best else (None, None)


def _aligned_sum(
    facts_by_concept: dict[str, list[FinancialFact]],
    concepts: tuple[str, ...],
    as_of_date: date,
    maximum_gap_days: int = 120,
) -> tuple[Decimal | None, date | None]:
    values = [
        _latest_instant(facts_by_concept, (concept,), as_of_date)
        for concept in concepts
    ]
    present = [
        (value, period_end)
        for value, period_end in values
        if value is not None and period_end
    ]
    if not present:
        return None, None
    latest_end = max(period_end for _, period_end in present)
    aligned = [
        (value, period_end)
        for value, period_end in present
        if abs((latest_end - period_end).days) <= maximum_gap_days
    ]
    return sum((value for value, _ in aligned), Decimal("0")), latest_end


def _short_term_investments(
    facts_by_concept: dict[str, list[FinancialFact]],
    as_of_date: date,
) -> tuple[Decimal | None, date | None]:
    aggregate, aggregate_end = _latest_instant(
        facts_by_concept, SHORT_TERM_INVESTMENT_CONCEPTS, as_of_date
    )
    if aggregate is not None:
        return aggregate, aggregate_end
    return _aligned_sum(
        facts_by_concept,
        SHORT_TERM_INVESTMENT_COMPONENT_CONCEPTS,
        as_of_date,
    )


def _flow_value(
    facts_by_concept: dict[str, list[FinancialFact]],
    concepts: tuple[str, ...],
    as_of_date: date,
) -> tuple[Decimal | None, date | None, str]:
    for concept in concepts:
        value, period_end, status = _ttm_value(
            facts_by_concept.get(concept, []), as_of_date
        )
        if value is not None:
            return value, period_end, status
    return None, None, "unavailable"


def _shares_metrics(
    facts_by_concept: dict[str, list[FinancialFact]], as_of_date: date
) -> tuple[Decimal | None, Decimal | None, date | None]:
    for concept in SHARE_CONCEPTS:
        candidates = _eligible_facts(
            facts_by_concept.get(concept, []), as_of_date, as_of_date
        )
        candidates = [fact for fact in candidates if "share" in fact.unit.lower()]
        if not candidates:
            continue
        latest = max(
            candidates, key=lambda fact: (fact.period_end, fact.filed_date or date.min)
        )
        target = latest.period_end - timedelta(days=365)
        priors = [
            fact
            for fact in candidates
            if abs((fact.period_end - target).days) <= 90
            and fact.period_end < latest.period_end
        ]
        prior = (
            min(priors, key=lambda fact: abs((fact.period_end - target).days))
            if priors
            else None
        )
        return (
            latest.value,
            _percent_change(latest.value, prior.value if prior else None),
            latest.period_end,
        )
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
    investments, investments_end = _short_term_investments(facts_by_concept, as_of_date)
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
        if current_assets is not None
        and current_liabilities not in (None, Decimal("0"))
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
    short_term_debt, short_term_debt_end = _latest_instant(
        facts_by_concept, SHORT_TERM_DEBT_CONCEPTS, as_of_date
    )
    commercial_paper, commercial_paper_end = _latest_instant(
        facts_by_concept, COMMERCIAL_PAPER_CONCEPTS, as_of_date
    )
    current_borrowings = short_term_debt
    current_borrowings_end = short_term_debt_end
    if current_borrowings is None:
        current_borrowings = debt_current
        current_borrowings_end = debt_current_end
    # A reported short-term-debt aggregate can already contain commercial
    # paper. Add the standalone commercial-paper tag only when no aggregate
    # exists, so the current portion is not double counted.
    if (
        short_term_debt is None
        and commercial_paper is not None
        and commercial_paper_end
    ):
        if (
            current_borrowings_end is None
            or abs((commercial_paper_end - current_borrowings_end).days) <= 120
        ):
            current_borrowings = (current_borrowings or Decimal("0")) + commercial_paper
            current_borrowings_end = max(
                value
                for value in (current_borrowings_end, commercial_paper_end)
                if value is not None
            )
    if debt_current is not None and debt_noncurrent is not None:
        total_debt = (current_borrowings or Decimal("0")) + debt_noncurrent
    elif short_term_debt is not None and debt_noncurrent is not None:
        total_debt = current_borrowings + debt_noncurrent
    elif debt_total is not None:
        additional_borrowings = Decimal("0")
        if short_term_debt is not None:
            additional_borrowings += short_term_debt
        elif commercial_paper is not None:
            additional_borrowings += commercial_paper
        total_debt = debt_total + additional_borrowings
    else:
        total_debt = (
            current_borrowings if current_borrowings is not None else debt_noncurrent
        )

    operating_cash_flow, ocf_end, ocf_status = _flow_value(
        facts_by_concept, OPERATING_CASH_FLOW_CONCEPTS, as_of_date
    )
    capex, capex_end, capex_status = _flow_value(
        facts_by_concept, CAPEX_CONCEPTS, as_of_date
    )
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
        short_term_debt_end,
        commercial_paper_end,
        ocf_end,
        capex_end,
        shares_end,
    ]
    latest_period = max(
        (value for value in period_candidates if value is not None), default=None
    )
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


def _feature_universe_statement(as_of_date: date):
    """Select every security that actually traded on the requested session."""
    return (
        select(Security)
        .join(DailyPriceBar, DailyPriceBar.ticker == Security.ticker)
        .where(DailyPriceBar.trade_date == as_of_date)
        .order_by(Security.ticker)
    )


def _reference_history_statement(as_of_date: date):
    cutoff = datetime.combine(as_of_date, time.max, tzinfo=timezone.utc)
    return (
        select(SecurityReferenceHistory)
        .join(
            DailyPriceBar,
            DailyPriceBar.ticker == SecurityReferenceHistory.ticker,
        )
        .where(
            DailyPriceBar.trade_date == as_of_date,
            SecurityReferenceHistory.observed_at_utc <= cutoff,
        )
        .order_by(
            SecurityReferenceHistory.ticker,
            SecurityReferenceHistory.observed_at_utc,
            SecurityReferenceHistory.id,
        )
    )


def _security_reference_values(security: Security) -> dict[str, Any]:
    return {field: getattr(security, field, None) for field in REFERENCE_FIELDS}


def _resolve_feature_securities(
    securities: Iterable[Security],
    history_rows: Iterable[SecurityReferenceHistory],
) -> list[FeatureSecurity]:
    """Resolve reference metadata without using today's active flag as eligibility."""
    history_by_ticker: dict[str, list[SecurityReferenceHistory]] = defaultdict(list)
    for row in history_rows:
        history_by_ticker[row.ticker].append(row)

    resolved: list[FeatureSecurity] = []
    for security in securities:
        current = _security_reference_values(security)
        historical: dict[str, Any] = {}
        observed_at: datetime | None = None
        for row in history_by_ticker.get(security.ticker, []):
            snapshot = row.snapshot or {}
            for field in REFERENCE_FIELDS:
                value = snapshot.get(field)
                if value is not None:
                    historical[field] = value
            observed_at = row.observed_at_utc

        missing_from_history = {
            field
            for field in REFERENCE_FIELDS
            if historical.get(field) is None and current.get(field) is not None
        }
        values = {**current, **historical}

        # A session price bar is the durable evidence that the symbol was
        # tradable on this date. Current inactive status must not remove it
        # from a historical feature universe.
        values["active"] = True
        imputed = not historical or bool(missing_from_history)

        if (
            values.get("market") != "stocks"
            or values.get("locale") != "us"
            or values.get("security_type") != "CS"
        ):
            continue

        resolved.append(
            FeatureSecurity(
                ticker=security.ticker,
                name=values.get("name"),
                market=values.get("market"),
                locale=values.get("locale"),
                currency=values.get("currency"),
                primary_exchange=values.get("primary_exchange"),
                security_type=values.get("security_type"),
                active=True,
                current_active=bool(current.get("active")),
                cik=values.get("cik"),
                composite_figi=values.get("composite_figi"),
                share_class_figi=values.get("share_class_figi"),
                sic_code=values.get("sic_code"),
                sic_description=values.get("sic_description"),
                fiscal_year_end=values.get("fiscal_year_end"),
                state_of_incorporation=values.get("state_of_incorporation"),
                reference_metadata_imputed=imputed,
                reference_observed_at_utc=observed_at,
            )
        )
    return resolved


def _feature_universe(session: Session, as_of_date: date) -> list[FeatureSecurity]:
    securities = session.scalars(_feature_universe_statement(as_of_date)).all()
    history_rows = session.scalars(_reference_history_statement(as_of_date)).all()
    return _resolve_feature_securities(securities, history_rows)


def _feature_date_is_complete(session: Session, as_of_date: date) -> bool:
    expected = len(_feature_universe(session, as_of_date))
    if expected == 0:
        return False
    actual = session.scalar(
        select(func.count(SecurityDailyFeature.id)).where(
            SecurityDailyFeature.as_of_date == as_of_date,
            SecurityDailyFeature.calculation_version == CALCULATION_VERSION,
        )
    )
    return int(actual or 0) == expected


def backfill_daily_features(
    session: Session,
    settings: Settings,
    start_date: date,
    end_date: date,
    resume: bool = False,
) -> dict[str, Any]:
    if start_date > end_date:
        raise ValueError("Feature backfill start date must be on or before end date")

    sessions = session.scalars(
        select(DailyPriceBar.trade_date)
        .where(
            DailyPriceBar.ticker == "QQQ",
            DailyPriceBar.trade_date.between(start_date, end_date),
        )
        .distinct()
        .order_by(DailyPriceBar.trade_date)
    ).all()
    if not sessions:
        raise RuntimeError(
            f"No stored QQQ market sessions between {start_date} and {end_date}"
        )

    completed: list[str] = []
    skipped: list[str] = []
    rows_written = 0
    for market_date in sessions:
        if resume and _feature_date_is_complete(session, market_date):
            skipped.append(market_date.isoformat())
            logger.info("Skipping complete feature date %s", market_date)
            continue
        _, written = calculate_daily_features(session, settings, market_date)
        completed.append(market_date.isoformat())
        rows_written += written

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "calculation_version": CALCULATION_VERSION,
        "market_sessions": len(sessions),
        "completed_sessions": len(completed),
        "skipped_sessions": len(skipped),
        "rows_written": rows_written,
        "completed_dates": completed,
        "skipped_dates": skipped,
    }


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
        source_rows = (
            session.scalar(
                select(func.count(DailyPriceBar.id)).where(
                    DailyPriceBar.trade_date == effective_date
                )
            )
            or 0
        )
        if source_rows < settings.massive_min_daily_results:
            raise RuntimeError(
                f"Market data is incomplete for {effective_date}: {source_rows} rows; "
                f"expected at least {settings.massive_min_daily_results}"
            )

        securities = _feature_universe(session, effective_date)
        seen = len(securities)
        tickers = {security.ticker for security in securities}
        ciks = {security.cik for security in securities if security.cik}
        cik_ticker_counts: dict[str, int] = defaultdict(int)
        for security in securities:
            if security.cik:
                cik_ticker_counts[security.cik] += 1
        history_start = effective_date - timedelta(days=400)

        price_query_tickers = tickers | {"QQQ"}
        price_rows = session.scalars(
            select(DailyPriceBar)
            .where(
                DailyPriceBar.ticker.in_(price_query_tickers),
                DailyPriceBar.trade_date.between(history_start, effective_date),
            )
            .order_by(DailyPriceBar.ticker, DailyPriceBar.trade_date)
        ).all()
        prices_by_ticker: dict[str, list[DailyPriceBar]] = defaultdict(list)
        for row in price_rows:
            prices_by_ticker[row.ticker].append(row)
        qqq_history = prices_by_ticker.get("QQQ", [])
        benchmark_20d_return = None
        if qqq_history:
            benchmark_metrics, _ = _price_metrics(qqq_history, effective_date)
            benchmark_20d_return = benchmark_metrics["price_change_20d_pct"]

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
        source_cutoff = datetime.now(timezone.utc)
        for position, security in enumerate(securities, start=1):
            price_history = prices_by_ticker.get(security.ticker, [])
            if not price_history:
                continue
            price_metrics, price_flags = _price_metrics(
                price_history, effective_date, benchmark_20d_return
            )
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
            if security.reference_metadata_imputed:
                metadata_flags.append("reference_metadata_imputed")
            if not security.current_active:
                metadata_flags.append("historical_active_inferred_from_price_bar")
            flags = sorted(set(price_flags + financial_flags + metadata_flags))
            output_rows.append(
                {
                    "ticker": security.ticker,
                    "as_of_date": effective_date,
                    "reference_name": security.name,
                    "reference_primary_exchange": security.primary_exchange,
                    "reference_security_type": security.security_type,
                    "reference_active": security.active,
                    "reference_sic_code": security.sic_code,
                    "reference_sic_description": security.sic_description,
                    **price_metrics,
                    **financial_metrics,
                    "calculation_version": CALCULATION_VERSION,
                    "quality_flags": flags,
                    "source_data_cutoff_utc": source_cutoff,
                    "source_manifest": {
                        "market_source": "massive",
                        "market_date": price_metrics["price_date"].isoformat(),
                        "financial_source": "sec-edgar",
                        "financial_filing_cutoff_date": effective_date.isoformat(),
                        "benchmark": "QQQ",
                        "feature_universe": "exact-session-price-bar-v2",
                        "security_reference": {
                            "name": security.name,
                            "primary_exchange": security.primary_exchange,
                            "security_type": security.security_type,
                            "active": security.active,
                            "current_active": security.current_active,
                            "sic_code": security.sic_code,
                            "sic_description": security.sic_description,
                            "metadata_imputed": security.reference_metadata_imputed,
                            "observed_at_utc": (
                                security.reference_observed_at_utc.isoformat()
                                if security.reference_observed_at_utc
                                else None
                            ),
                        },
                    },
                }
            )
            if position % 1000 == 0:
                logger.info(
                    "Calculated features for %s/%s securities",
                    position,
                    len(securities),
                )

        for start in range(0, len(output_rows), 500):
            batch = output_rows[start : start + 500]
            statement = insert(SecurityDailyFeature).values(batch)
            excluded = statement.excluded
            update_values = {
                column.name: getattr(excluded, column.name)
                for column in SecurityDailyFeature.__table__.columns
                if column.name
                not in {
                    "id",
                    "ticker",
                    "as_of_date",
                    "calculation_version",
                    "calculated_at_utc",
                }
            }
            update_values["calculated_at_utc"] = func.now()
            statement = statement.on_conflict_do_update(
                constraint="uq_security_features_ticker_date_version",
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
                "feature_universe_rows": seen,
                "feature_universe": "exact-session-price-bar-v2",
                "calculation_version": CALCULATION_VERSION,
                "price_rows_read": len(price_rows),
                "financial_facts_read": len(fact_rows),
            },
        )
        return seen, written
    except Exception as error:
        tracker.fail(error, seen, written)
        raise
