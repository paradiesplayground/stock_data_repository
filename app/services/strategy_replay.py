import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.industry_taxonomy import TAXONOMY_VERSION, resolve_industry_groups
from app.models import DailyPriceBar, SecurityDailyFeature, StrategyRun
from app.services.strategy_tracking import record_strategy_run

STRATEGY_KEY = "fallen-growth-swing"
STRATEGY_VERSION = "1.1.0"
FEATURE_VERSION = "1.3.0"
REPLAY_MODEL = "deterministic-replay-v1"

MIN_PRICE = Decimal("5")
MIN_MARKET_CAP = Decimal("100000000")
STANDARD_MARKET_CAP = Decimal("250000000")
MIN_TTM_GROWTH = Decimal("40")
MIN_QUARTER_GROWTH = Decimal("40")
MAX_PRICE_CHANGE_12W = Decimal("-20")
MIN_DOLLAR_VOLUME = Decimal("30000000")
MIN_CASH_RUNWAY = Decimal("12")
MAX_ENTRY_GAP_PCT = Decimal("5")


def replay_configuration() -> dict[str, Any]:
    resolved, prefixes = resolve_industry_groups(["Healthcare"])
    return {
        "schema_version": 1,
        "model": REPLAY_MODEL,
        "feature_calculation_version": FEATURE_VERSION,
        "universe": {
            "exchanges": ["XNAS", "XNYS"],
            "security_type": "CS",
            "exclude_otc": True,
            "minimum_price": str(MIN_PRICE),
            "minimum_market_cap": str(MIN_MARKET_CAP),
            "standard_market_cap": str(STANDARD_MARKET_CAP),
            "exclude_industry_groups": [item["key"] for item in resolved],
            "excluded_sic_prefixes": prefixes,
            "industry_taxonomy_version": TAXONOMY_VERSION,
        },
        "hard_thresholds": {
            "minimum_ttm_revenue_growth_pct": str(MIN_TTM_GROWTH),
            "minimum_quarter_revenue_growth_pct": str(MIN_QUARTER_GROWTH),
            "maximum_price_change_12w_pct": str(MAX_PRICE_CHANGE_12W),
            "minimum_avg_dollar_volume_20d": str(MIN_DOLLAR_VOLUME),
            "minimum_cash_runway_months": str(MIN_CASH_RUNWAY),
        },
        "scoring_model": "mechanical-subset-of-skill-rubric-v1",
        "entry_model": {
            "trigger": "0.1% above rolling 20-session high",
            "initial_stop": "higher of rolling 20-session low or two ATR below trigger",
            "target_one_r": "2",
            "target_two_r": "3",
            "maximum_gap_above_trigger_pct": str(MAX_ENTRY_GAP_PCT),
        },
        "known_limitations": [
            "no historical catalyst score",
            "no historical bid-ask spread or public-float score",
            "no automated going-concern text review",
            "no customer-concentration or organic-growth score",
            "unknown SIC classifications are retained as incomplete and never actionable",
        ],
    }


def _json_decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _sic_excluded(sic_code: str | None, prefixes: list[str]) -> bool | None:
    if not sic_code or not str(sic_code).strip().isdigit():
        return None
    normalized = str(sic_code).strip().zfill(4)
    return any(normalized.startswith(prefix) for prefix in prefixes)


def _growth_points(value: Decimal | None) -> int:
    if value is None or value < 40:
        return 0
    if value >= 100:
        return 10
    if value >= 75:
        return 8
    return 6


def _liquidity_points(value: Decimal | None) -> int:
    if value is None or value < 30_000_000:
        return 0
    if value >= 100_000_000:
        return 9
    if value >= 50_000_000:
        return 7
    return 5


def _risk_multiplier(market_cap: Decimal) -> Decimal:
    if market_cap >= 1_000_000_000:
        return Decimal("1")
    if market_cap >= 500_000_000:
        return Decimal("0.75")
    if market_cap >= 250_000_000:
        return Decimal("0.50")
    return Decimal("0.25")


def _risk_tier(market_cap: Decimal) -> str:
    if market_cap >= 1_000_000_000:
        return "standard"
    if market_cap >= 500_000_000:
        return "elevated"
    if market_cap >= 250_000_000:
        return "high"
    return "speculative"


def score_feature(
    feature: SecurityDailyFeature,
    *,
    constructive_volume: bool,
    excluded_sic_prefixes: list[str],
) -> dict[str, Any] | None:
    """Return a reproducible replay candidate from stored point-in-time fields."""
    required_values = (
        feature.close,
        feature.approximate_market_cap,
        feature.revenue_ttm_yoy_pct,
        feature.latest_quarter_revenue_yoy_pct,
        feature.price_change_12w_pct,
        feature.avg_dollar_volume_20d,
    )
    if any(value is None for value in required_values):
        return None
    if (
        feature.reference_primary_exchange not in {"XNAS", "XNYS"}
        or feature.reference_security_type != "CS"
        or feature.reference_active is not True
        or feature.close < MIN_PRICE
        or feature.approximate_market_cap < MIN_MARKET_CAP
        or feature.revenue_ttm_yoy_pct < MIN_TTM_GROWTH
        or feature.latest_quarter_revenue_yoy_pct < MIN_QUARTER_GROWTH
        or feature.price_change_12w_pct > MAX_PRICE_CHANGE_12W
        or feature.avg_dollar_volume_20d < MIN_DOLLAR_VOLUME
    ):
        return None

    excluded = _sic_excluded(feature.reference_sic_code, excluded_sic_prefixes)
    if excluded is True:
        return None

    reasons: list[str] = []
    warnings: list[str] = []
    revenue_points = _growth_points(feature.revenue_ttm_yoy_pct) + _growth_points(
        feature.latest_quarter_revenue_yoy_pct
    )

    price_points = 4
    if feature.drawdown_52w_pct is not None and Decimal(
        "-60"
    ) <= feature.drawdown_52w_pct <= Decimal("-30"):
        price_points += 4
    if feature.ema_10 is not None and feature.close >= feature.ema_10:
        price_points += 3
    if feature.ema_20 is not None and feature.close >= feature.ema_20:
        price_points += 4

    liquidity_points = _liquidity_points(feature.avg_dollar_volume_20d)
    financial_points = 0
    self_funding = (
        feature.free_cash_flow_ttm is not None and feature.free_cash_flow_ttm >= 0
    )
    if self_funding:
        # Runway is intentionally null for non-burning companies in the
        # derived-data contract; positive FCF satisfies both runway buckets.
        financial_points += 6
    elif feature.cash_runway_months is not None and feature.cash_runway_months >= 12:
        financial_points += 4
        if feature.cash_runway_months >= 24:
            financial_points += 2
    if feature.share_count_yoy_pct is not None and feature.share_count_yoy_pct < 15:
        financial_points += 3
    if (
        feature.total_debt is not None
        and feature.cash_and_short_term_investments is not None
        and feature.total_debt <= feature.cash_and_short_term_investments
    ):
        financial_points += 3
    if feature.free_cash_flow_ttm is not None and feature.free_cash_flow_ttm >= 0:
        financial_points += 3

    technical_points = 0
    if (
        feature.low_20d is not None
        and feature.low_60d is not None
        and feature.low_20d > feature.low_60d
    ):
        technical_points += 3
    if (feature.ema_10 is not None and feature.close >= feature.ema_10) or (
        feature.ema_20 is not None and feature.close >= feature.ema_20
    ):
        technical_points += 3
    if (
        feature.distance_to_20d_high_pct is not None
        and feature.distance_to_20d_high_pct >= Decimal("-3")
    ):
        technical_points += 3
    if constructive_volume:
        technical_points += 3
    if (
        feature.relative_return_20d_vs_qqq_pct is not None
        and feature.relative_return_20d_vs_qqq_pct > 0
    ):
        technical_points += 3

    score_components = {
        "revenue_growth": revenue_points,
        "revenue_quality": 0,
        "price_setup": price_points,
        "liquidity": liquidity_points,
        "financial_durability": financial_points,
        "catalyst": 0,
        "technical_confirmation": technical_points,
    }
    score = sum(score_components.values())

    incomplete = False
    if excluded is None:
        incomplete = True
        warnings.append("unknown_sic_exclusion_status")
    if feature.cash_runway_months is None and not self_funding:
        incomplete = True
        warnings.append("missing_cash_runway")
    elif (
        feature.cash_runway_months is not None
        and feature.cash_runway_months < MIN_CASH_RUNWAY
    ):
        reasons.append("cash_runway_below_12_months")
    if feature.high_20d is None or feature.low_20d is None or feature.atr_14 is None:
        incomplete = True
        warnings.append("missing_trade_plan_price_fields")

    rejected = bool(reasons)
    actionable = (
        not incomplete
        and not rejected
        and score >= 60
        and technical_points >= 9
        and (feature.rsi_14 is None or feature.rsi_14 < 75)
    )
    stage = "rejected" if rejected else "incomplete" if incomplete else "qualified"
    action = "remove" if rejected else "actionable" if actionable else "keep-watching"

    trade_plan = None
    if not incomplete and not rejected:
        trigger = feature.high_20d * Decimal("1.001")
        atr_stop = trigger - (feature.atr_14 * Decimal("2"))
        stop = max(feature.low_20d, atr_stop)
        if stop >= trigger:
            stop = trigger - feature.atr_14
        risk_per_share = trigger - stop
        if risk_per_share > 0:
            trade_plan = {
                "entry_order": "buy-stop",
                "entry_trigger": str(trigger),
                "maximum_entry_price": str(
                    trigger * (Decimal("1") + MAX_ENTRY_GAP_PCT / Decimal("100"))
                ),
                "initial_stop": str(stop),
                "target_one": str(trigger + risk_per_share * Decimal("2")),
                "target_two": str(trigger + risk_per_share * Decimal("3")),
                "risk_tier": _risk_tier(feature.approximate_market_cap),
                "risk_multiplier": str(
                    _risk_multiplier(feature.approximate_market_cap)
                ),
            }
        else:
            stage = "incomplete"
            action = "keep-watching"
            warnings.append("invalid_trade_plan_risk")

    return {
        "ticker": feature.ticker,
        "stage": stage,
        "action": action,
        "score": score,
        "score_components": score_components,
        "metrics": {
            "close": _json_decimal(feature.close),
            "market_cap": _json_decimal(feature.approximate_market_cap),
            "ttm_revenue_growth_pct": _json_decimal(feature.revenue_ttm_yoy_pct),
            "quarter_revenue_growth_pct": _json_decimal(
                feature.latest_quarter_revenue_yoy_pct
            ),
            "price_change_12w_pct": _json_decimal(feature.price_change_12w_pct),
            "drawdown_52w_pct": _json_decimal(feature.drawdown_52w_pct),
            "avg_dollar_volume_20d": _json_decimal(feature.avg_dollar_volume_20d),
            "cash_runway_months": _json_decimal(feature.cash_runway_months),
            "positive_free_cash_flow": self_funding,
            "rsi_14": _json_decimal(feature.rsi_14),
            "relative_volume_20d": _json_decimal(feature.relative_volume_20d),
        },
        "reasons": reasons + warnings,
        "trade_plan": trade_plan,
        "payload": {
            "in_raw_pool": True,
            "in_qualified_watchlist": stage == "qualified",
            "mechanical_replay": True,
            "qualitative_review_performed": False,
        },
    }


def _constructive_volume_tickers(
    session: Session, as_of_date: date, tickers: set[str]
) -> set[str]:
    if not tickers:
        return set()
    latest_rows = session.execute(
        select(DailyPriceBar.ticker, DailyPriceBar.close, DailyPriceBar.volume).where(
            DailyPriceBar.trade_date == as_of_date,
            DailyPriceBar.ticker.in_(tickers),
        )
    ).all()
    prior_date = session.scalar(
        select(func.max(DailyPriceBar.trade_date)).where(
            DailyPriceBar.ticker == "QQQ", DailyPriceBar.trade_date < as_of_date
        )
    )
    if prior_date is None:
        return set()
    prior_close = dict(
        session.execute(
            select(DailyPriceBar.ticker, DailyPriceBar.close).where(
                DailyPriceBar.trade_date == prior_date,
                DailyPriceBar.ticker.in_(tickers),
            )
        ).all()
    )
    features = dict(
        session.execute(
            select(
                SecurityDailyFeature.ticker,
                SecurityDailyFeature.avg_volume_20d,
            ).where(
                SecurityDailyFeature.as_of_date == as_of_date,
                SecurityDailyFeature.calculation_version == FEATURE_VERSION,
                SecurityDailyFeature.ticker.in_(tickers),
            )
        ).all()
    )
    return {
        ticker
        for ticker, close, volume in latest_rows
        if ticker in prior_close
        and features.get(ticker)
        and close > prior_close[ticker]
        and volume >= features[ticker] * Decimal("1.5")
    }


def replay_strategy_date(session: Session, as_of_date: date) -> dict[str, Any]:
    configuration = replay_configuration()
    excluded_prefixes = configuration["universe"]["excluded_sic_prefixes"]
    features = session.scalars(
        select(SecurityDailyFeature)
        .where(
            SecurityDailyFeature.as_of_date == as_of_date,
            SecurityDailyFeature.calculation_version == FEATURE_VERSION,
            SecurityDailyFeature.price_date == as_of_date,
        )
        .order_by(SecurityDailyFeature.ticker)
    ).all()
    if not features:
        raise RuntimeError(
            f"No {FEATURE_VERSION} feature snapshot exists for {as_of_date}"
        )
    constructive = _constructive_volume_tickers(
        session, as_of_date, {item.ticker for item in features}
    )
    candidates = [
        candidate
        for feature in features
        if (
            candidate := score_feature(
                feature,
                constructive_volume=feature.ticker in constructive,
                excluded_sic_prefixes=excluded_prefixes,
            )
        )
        is not None
    ]
    candidates.sort(key=lambda item: (-item["score"], item["ticker"]))
    source_cutoff = max(
        (
            item.source_data_cutoff_utc
            for item in features
            if item.source_data_cutoff_utc is not None
        ),
        default=None,
    )
    summary = {
        "model": REPLAY_MODEL,
        "feature_rows_reviewed": len(features),
        "raw_candidate_count": len(candidates),
        "qualified_count": sum(item["stage"] == "qualified" for item in candidates),
        "incomplete_count": sum(item["stage"] == "incomplete" for item in candidates),
        "rejected_count": sum(item["stage"] == "rejected" for item in candidates),
        "actionable_count": sum(item["action"] == "actionable" for item in candidates),
        "qualitative_review_performed": False,
    }
    result = record_strategy_run(
        session,
        strategy_key=STRATEGY_KEY,
        strategy_version=STRATEGY_VERSION,
        strategy_name="Fallen growth swing — deterministic replay",
        as_of_date=as_of_date.isoformat(),
        run_type="backtest",
        idempotency_key=(
            f"{STRATEGY_KEY}:{STRATEGY_VERSION}:{as_of_date}:"
            f"{FEATURE_VERSION}:{REPLAY_MODEL}"
        ),
        configuration=configuration,
        filters={"exclude_industry_groups": ["Healthcare"]},
        candidates=candidates,
        summary=summary,
        feature_calculation_version=FEATURE_VERSION,
        data_cutoff_at_utc=source_cutoff.isoformat() if source_cutoff else None,
        notes=(
            "Mechanical point-in-time replay. Historical qualitative catalyst, "
            "going-concern, customer concentration, spread, and float reviews are "
            "not inferred."
        ),
    )
    return {**result, "as_of_date": as_of_date.isoformat(), **summary}


def replay_strategy_range(
    session: Session,
    start_date: date,
    end_date: date,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    if start_date > end_date:
        raise ValueError("Replay start date must be on or before end date")
    sessions = session.scalars(
        select(DailyPriceBar.trade_date)
        .where(
            DailyPriceBar.ticker == "QQQ",
            DailyPriceBar.trade_date.between(start_date, end_date),
        )
        .distinct()
        .order_by(DailyPriceBar.trade_date)
    ).all()
    completed: list[str] = []
    skipped: list[str] = []
    actionable = raw_candidates = 0
    for market_date in sessions:
        key = (
            f"{STRATEGY_KEY}:{STRATEGY_VERSION}:{market_date}:"
            f"{FEATURE_VERSION}:{REPLAY_MODEL}"
        )
        if resume and session.scalar(
            select(StrategyRun.run_id).where(StrategyRun.idempotency_key == key)
        ):
            skipped.append(market_date.isoformat())
            continue
        result = replay_strategy_date(session, market_date)
        completed.append(market_date.isoformat())
        actionable += result["actionable_count"]
        raw_candidates += result["raw_candidate_count"]
    source_runs = session.scalars(
        select(StrategyRun.payload_hash)
        .where(
            StrategyRun.run_type == "backtest",
            StrategyRun.as_of_date.between(start_date, end_date),
            StrategyRun.feature_calculation_version == FEATURE_VERSION,
            StrategyRun.idempotency_key.like(
                f"{STRATEGY_KEY}:{STRATEGY_VERSION}:%:{FEATURE_VERSION}:{REPLAY_MODEL}"
            ),
        )
        .order_by(StrategyRun.as_of_date)
    ).all()
    source_runs_hash = hashlib.sha256(
        json.dumps(source_runs, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "strategy_key": STRATEGY_KEY,
        "strategy_version": STRATEGY_VERSION,
        "replay_model": REPLAY_MODEL,
        "feature_calculation_version": FEATURE_VERSION,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "market_sessions": len(sessions),
        "completed_sessions": len(completed),
        "skipped_sessions": len(skipped),
        "raw_candidates_in_completed_sessions": raw_candidates,
        "actionable_signals_in_completed_sessions": actionable,
        "source_runs_hash": source_runs_hash,
        "completed_dates": completed,
        "skipped_dates": skipped,
    }
