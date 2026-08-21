import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.mcp_queries import (
    get_data_freshness,
    get_security_features,
    query_security_features,
)
from app.models import DailyPriceBar
from app.services.strategy_tracking import get_strategy_run, list_strategy_runs

STRATEGY_KEY = "dynamic_swing_buy_alerts"
STRATEGY_VERSION = "0.7"
SKILL_VERSION = "1.5.1"
DECISION_CONTRACT_VERSION = "0.8"
MINIMUM_FEATURE_VERSION = (1, 4, 0)

STRATEGY_CONFIGURATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "alerts"
    / "dynamic-swing-buy-alerts-v0.7.json"
)
STRATEGY_CONFIGURATION = json.loads(
    STRATEGY_CONFIGURATION_PATH.read_text(encoding="utf-8")
)

QUALITATIVE_EVIDENCE_REQUIREMENTS = [
    "going_concern_and_auditor_language",
    "revenue_quality_and_customer_concentration",
    "offerings_atm_convertibles_and_warrants",
    "near_term_debt_and_refinancing_risk",
    "gross_margin_and_guidance_trend",
    "near_term_catalyst_and_event_risk",
    "bid_ask_spread_and_public_float",
    "chart_structure_and_directional_volume_review",
]


def _version_tuple(value: str) -> tuple[int, int, int]:
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError as error:
        raise RuntimeError(f"unsupported feature calculation version: {value}") from error
    if len(parts) != 3:
        raise RuntimeError(f"unsupported feature calculation version: {value}")
    return parts


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _json_decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _spy_market_regime(session: Session, as_of_date: date) -> dict[str, Any]:
    rows = session.execute(
        select(DailyPriceBar.trade_date, DailyPriceBar.close)
        .where(
            DailyPriceBar.ticker == "SPY",
            DailyPriceBar.trade_date <= as_of_date,
        )
        .order_by(desc(DailyPriceBar.trade_date))
        .limit(50)
    ).all()
    if len(rows) < 50 or rows[0][0] != as_of_date:
        return {
            "benchmark_ticker": "SPY",
            "as_of_date": as_of_date.isoformat(),
            "status": "unavailable",
            "gate_passed": False,
            "reason": "50 completed SPY sessions ending on the alert date are required",
        }
    latest_close = Decimal(str(rows[0][1]))
    sma_50 = sum(Decimal(str(close)) for _, close in rows) / Decimal("50")
    return {
        "benchmark_ticker": "SPY",
        "as_of_date": as_of_date.isoformat(),
        "completed_sessions": 50,
        "latest_close": _json_decimal(latest_close),
        "sma_50": _json_decimal(sma_50),
        "status": "pass" if latest_close > sma_50 else "block",
        "gate_passed": latest_close > sma_50,
    }


def _prior_run(session: Session, as_of_date: date) -> dict[str, Any] | None:
    listed = list_strategy_runs(
        session,
        strategy_key=STRATEGY_KEY,
        strategy_version=STRATEGY_VERSION,
        run_type="as_run",
        end_date=(as_of_date - timedelta(days=1)).isoformat(),
        limit=1,
    )
    if not listed["items"]:
        return None
    return get_strategy_run(session, listed["items"][0]["run_id"])


def _deterministic_candidate(item: dict[str, Any], market_gate: bool) -> dict[str, Any]:
    close = _decimal(item.get("close"))
    high_20d = _decimal(item.get("high_20d"))
    low_20d = _decimal(item.get("low_20d"))
    atr_14 = _decimal(item.get("atr_14"))
    trigger = high_20d * Decimal("1.001") if high_20d is not None else None
    invalidation = None
    if trigger is not None and low_20d is not None and atr_14 is not None:
        invalidation = max(low_20d, trigger - atr_14 * Decimal("2"))
        if invalidation >= trigger:
            invalidation = trigger - atr_14
    distance = (
        ((trigger - close) / trigger) * Decimal("100")
        if trigger is not None and close is not None
        else None
    )
    relative_strength = _decimal(item.get("relative_return_20d_vs_qqq_pct"))
    quality_flags = sorted(set(item.get("quality_flags") or []))
    risk_flags: list[str] = []
    if item.get("sic_code") is None:
        risk_flags.append("unknown_sic_exclusion_status")
    runway = _decimal(item.get("cash_runway_months"))
    free_cash_flow = _decimal(item.get("free_cash_flow_ttm"))
    if runway is None and (free_cash_flow is None or free_cash_flow < 0):
        risk_flags.append("cash_runway_unavailable")
    elif runway is not None and runway < Decimal("12"):
        risk_flags.append("cash_runway_below_12_months")
    dilution = _decimal(item.get("share_count_yoy_pct"))
    if dilution is not None and dilution >= Decimal("15"):
        risk_flags.append("share_count_growth_at_or_above_15_pct")

    return {
        "ticker": item["ticker"],
        "company": item.get("company"),
        "deterministic_metrics": item,
        "suggested_trigger_price": _json_decimal(trigger),
        "suggested_invalidation_price": _json_decimal(invalidation),
        "distance_to_trigger_pct": _json_decimal(distance),
        "represented_gates": {
            "market_regime_gate_passed": market_gate,
            "relative_strength_gate_passed": bool(
                relative_strength is not None and relative_strength > 0
            ),
            "price_at_or_above_trigger": bool(distance is not None and distance <= 0),
            "price_within_five_pct_below_trigger": bool(
                distance is not None and Decimal("0") <= distance <= Decimal("5")
            ),
        },
        "deterministic_risk_flags": sorted(set(risk_flags)),
        "repository_quality_flags": quality_flags,
        "qualitative_evidence_required": list(QUALITATIVE_EVIDENCE_REQUIREMENTS),
    }


def _metric_changes(
    current: dict[str, Any], prior: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    if prior is None:
        return {}
    prior_metrics = prior.get("metrics") or {}
    fields = (
        "close",
        "price_change_12w_pct",
        "relative_return_20d_vs_qqq_pct",
        "revenue_ttm_yoy_pct",
        "latest_quarter_revenue_yoy_pct",
        "cash_runway_months",
        "share_count_yoy_pct",
        "latest_source_filing_date",
    )
    return {
        field: {"previous": prior_metrics.get(field), "current": current.get(field)}
        for field in fields
        if prior_metrics.get(field) != current.get(field)
    }


def _research_plan(
    candidate: dict[str, Any],
    prior: dict[str, Any] | None,
    prior_evidence: list[dict[str, Any]],
    *,
    is_new: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    if is_new:
        reasons.append("new_raw_pool_candidate")
    daily_move = _decimal(candidate["deterministic_metrics"].get("daily_return_pct"))
    if daily_move is not None and abs(daily_move) >= Decimal("5"):
        reasons.append("material_daily_move")
    changes = _metric_changes(candidate["deterministic_metrics"], prior)
    filing_change = changes.get("latest_source_filing_date")
    if (
        filing_change
        and filing_change["previous"] is not None
        and filing_change["current"] is not None
        and str(filing_change["current"]) > str(filing_change["previous"])
    ):
        reasons.append("fresh_source_filing")
    if prior and prior.get("buyability_status") in {"BUY_NOW", "ALMOST_READY"}:
        reasons.append("prior_near_buyable_status")
    if candidate["deterministic_risk_flags"]:
        reasons.append("deterministic_risk_flag")
    if not prior_evidence:
        reasons.append("no_reusable_prior_evidence")

    if any(
        reason
        in {
            "new_raw_pool_candidate",
            "material_daily_move",
            "fresh_source_filing",
            "prior_near_buyable_status",
            "deterministic_risk_flag",
        }
        for reason in reasons
    ):
        priority = "high"
    elif not prior_evidence or not prior:
        priority = "normal"
    else:
        priority = "low"
    return {
        "ticker": candidate["ticker"],
        "priority": priority,
        "reasons": reasons or ["unchanged_candidate_review"],
        "prior_buyability_status": prior.get("buyability_status") if prior else None,
        "metric_changes": changes,
        "reusable_prior_evidence": prior_evidence,
        "qualitative_evidence_required": candidate[
            "qualitative_evidence_required"
        ],
    }


def prepare_daily_stock_alert(
    session: Session,
    settings: Settings,
    *,
    as_of_date: str,
    limit: int = 100,
    exclude_industry_groups: list[str] | None = None,
) -> dict[str, Any]:
    """Build the deterministic half of a hybrid production alert."""
    requested_date = date.fromisoformat(as_of_date)
    freshness = get_data_freshness(session, settings)
    if freshness.get("expected_market_date") != as_of_date:
        raise ValueError(
            "as_of_date must match the current expected market date "
            + str(freshness.get("expected_market_date") or "<unknown>")
        )
    if not freshness.get("ready_for_screening"):
        detail = "; ".join(str(item) for item in freshness.get("freshness_issues") or [])
        raise RuntimeError("data is not ready for screening: " + (detail or "stale data"))

    exclusions = exclude_industry_groups or ["Healthcare"]
    pool = query_security_features(
        session,
        as_of_date=as_of_date,
        min_price=5,
        min_market_cap=100_000_000,
        min_ttm_revenue_growth_pct=40,
        min_quarter_revenue_growth_pct=40,
        max_price_change_12w_pct=-20,
        min_avg_dollar_volume_20d=30_000_000,
        exclude_industry_groups=exclusions,
        limit=limit,
    )
    if pool["as_of_date"] != as_of_date:
        raise RuntimeError("deterministic feature query did not resolve to the requested date")
    feature_version = str(pool.get("calculation_version") or "")
    if _version_tuple(feature_version) < MINIMUM_FEATURE_VERSION:
        raise RuntimeError("current alerts require feature calculation version 1.4.0 or later")

    regime = _spy_market_regime(session, requested_date)
    prior = _prior_run(session, requested_date)
    prior_candidates = {
        item["ticker"]: item for item in (prior or {}).get("candidates") or []
    }
    current_tickers = {item["ticker"] for item in pool["items"]}
    prior_raw_tickers = {
        ticker
        for ticker, item in prior_candidates.items()
        if (item.get("payload") or {}).get("in_raw_pool", True)
    }
    comparison = {
        "baseline": prior is None,
        "previous_run_id": prior.get("run_id") if prior else None,
        "previous_as_of_date": prior.get("as_of_date") if prior else None,
        "new_tickers": sorted(current_tickers - prior_raw_tickers),
        "continuing_tickers": sorted(current_tickers & prior_raw_tickers),
        "dropped_tickers": sorted(prior_raw_tickers - current_tickers),
    }
    prior_evidence_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for evidence in (prior or {}).get("evidence") or []:
        ticker = str(evidence.get("ticker") or "").strip().upper()
        if ticker:
            prior_evidence_by_ticker.setdefault(ticker, []).append(evidence)
    prepared_candidates = [
        _deterministic_candidate(item, regime["gate_passed"])
        for item in pool["items"]
    ]
    research_queue = [
        _research_plan(
            candidate,
            prior_candidates.get(candidate["ticker"]),
            prior_evidence_by_ticker.get(candidate["ticker"], []),
            is_new=candidate["ticker"] in comparison["new_tickers"],
        )
        for candidate in prepared_candidates
    ]
    priority_order = {"high": 0, "normal": 1, "low": 2}
    research_queue.sort(key=lambda item: (priority_order[item["priority"]], item["ticker"]))
    dropped_reviews = [
        {
            "ticker": ticker,
            "priority": "high",
            "reasons": ["dropped_from_raw_pool"],
            "prior_candidate": prior_candidates[ticker],
            "current_feature_check": get_security_features(
                session,
                ticker,
                as_of_date=as_of_date,
                calculation_version=feature_version,
            ),
            "reusable_prior_evidence": prior_evidence_by_ticker.get(ticker, []),
        }
        for ticker in comparison["dropped_tickers"]
    ]
    cutoff = max(
        (item.get("source_data_cutoff_utc") for item in pool["items"] if item.get("source_data_cutoff_utc")),
        default=None,
    )
    filters = {
        "exclude_industry_groups": exclusions,
        "resolved_industry_groups": pool["excluded_industry_groups"],
        "excluded_sic_prefixes": pool["excluded_sic_prefixes"],
        "limit": limit,
    }
    return {
        "status": "prepared",
        "workflow": "hybrid_deterministic_plus_qualitative",
        "as_of_date": as_of_date,
        "strategy_key": STRATEGY_KEY,
        "strategy_version": STRATEGY_VERSION,
        "skill_version": SKILL_VERSION,
        "decision_contract_version": DECISION_CONTRACT_VERSION,
        "freshness": freshness,
        "market_regime": regime,
        "comparison": comparison,
        "raw_candidate_count": pool["count"],
        "candidates": prepared_candidates,
        "research_queue": research_queue,
        "dropped_candidate_reviews": dropped_reviews,
        "run_template": {
            "strategy_key": STRATEGY_KEY,
            "strategy_version": STRATEGY_VERSION,
            "strategy_name": "Dynamic swing buy alerts",
            "as_of_date": as_of_date,
            "run_type": "as_run",
            "idempotency_key": ":".join(
                [STRATEGY_KEY, STRATEGY_VERSION, as_of_date, feature_version, cutoff or "no-cutoff"]
            ),
            "configuration": STRATEGY_CONFIGURATION,
            "filters": filters,
            "decision_contract_version": DECISION_CONTRACT_VERSION,
            "feature_calculation_version": feature_version,
            "data_cutoff_at_utc": cutoff,
            "candidates": [],
            "evidence": [],
            "summary": {
                "comparison": comparison,
                "raw_candidate_count": pool["count"],
                "market_regime": regime,
            },
            "report_markdown": None,
        },
        "finalization_requirements": {
            "instruction": (
                "Research the listed qualitative evidence, create canonical v0.8 candidates, "
                "complete summary and report_markdown, then pass run_template as run_payload "
                "to run_daily_stock_alert."
            ),
            "required_candidate_fields": [
                "screen_bucket",
                "technical_state",
                "buyability_status",
                "status_reason",
                "buy_conditions",
                "remaining_gate_count",
                "current_price",
                "technical_gate_passed",
                "market_regime_gate_passed",
            ],
        },
    }
