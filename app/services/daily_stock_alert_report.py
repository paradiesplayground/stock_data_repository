"""Deterministic report enrichment for the daily stock alert."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
from typing import Any


_ALERT_CONFIGURATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "alerts"
    / "dynamic-swing-buy-alerts-v0.8.json"
)
_HARD_THRESHOLDS = json.loads(_ALERT_CONFIGURATION_PATH.read_text(encoding="utf-8"))["hard_thresholds"]


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _points(value: Decimal | None, *, floor: Decimal, ceiling: Decimal, maximum: int) -> int:
    if value is None or ceiling <= floor:
        return 0
    return int((_clamp(value, floor, ceiling) - floor) * Decimal(maximum) / (ceiling - floor)).__floor__()


def _money(value: Any) -> str:
    parsed = _decimal(value)
    return f"${parsed.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}" if parsed is not None else "not available"


def _percent(value: Any) -> str:
    parsed = _decimal(value)
    return f"{parsed.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%" if parsed is not None else "not available"


def _implied_52w_high(metrics: dict[str, Any]) -> Decimal | None:
    """Recover the observed 52-week high from close and saved drawdown."""
    close = _decimal(metrics.get("close"))
    drawdown = _decimal(metrics.get("drawdown_52w_pct"))
    if close is None or close <= 0 or drawdown is None:
        return None
    denominator = Decimal("1") + (drawdown / Decimal("100"))
    if denominator <= 0:
        return None
    return (close / denominator).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def structural_targets(*, trigger: Any, metrics: dict[str, Any]) -> list[dict[str, str]]:
    """Return observed resistance levels above entry without inventing fixed-R targets."""
    entry = _decimal(trigger)
    if entry is None:
        return []

    high_60d = _decimal(metrics.get("high_60d"))
    high_52w = _implied_52w_high(metrics)
    targets: list[dict[str, str]] = []

    if high_60d is not None and high_60d > entry:
        targets.append({"price": str(high_60d), "basis": "60-day resistance"})

    if high_52w is not None and high_52w > entry:
        # The 60-day high can also be the 52-week high. Two labels on the same
        # price are not two useful targets, so dedupe at displayed-cent precision.
        duplicate = any(
            _decimal(item["price"]) is not None
            and _decimal(item["price"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            == high_52w.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            for item in targets
        )
        if not duplicate:
            targets.append({"price": str(high_52w), "basis": "52-week resistance"})

    return targets[:2]


def report_enrichment(
    *,
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    research: Any | None = None,
) -> dict[str, Any]:
    """Score a setup from saved deterministic facts and optional saved evidence."""
    metrics = snapshot.get("deterministic_metrics") or {}
    trigger = _decimal(snapshot.get("suggested_trigger_price"))
    stop = _decimal(snapshot.get("suggested_invalidation_price"))
    distance = _decimal(snapshot.get("distance_to_trigger_pct"))
    targets = structural_targets(trigger=trigger, metrics=metrics)
    risk = trigger - stop if trigger is not None and stop is not None else None
    target_details = []
    if risk is not None and risk > 0:
        for target in targets:
            price = _decimal(target["price"])
            if price is not None and price > trigger:
                target_details.append({
                    **target,
                    "r_multiple": str(((price - trigger) / risk).quantize(Decimal("0.01"))),
                })

    components = {
        "growth": min(20, _points(_decimal(metrics.get("revenue_ttm_yoy_pct")), floor=Decimal("40"), ceiling=Decimal("140"), maximum=12) + _points(_decimal(metrics.get("latest_quarter_revenue_yoy_pct")), floor=Decimal("40"), ceiling=Decimal("140"), maximum=8)),
        "trend": min(20, _points(_decimal(metrics.get("relative_return_20d_vs_qqq_pct")), floor=Decimal("0"), ceiling=Decimal("25"), maximum=12) + _points(_decimal(metrics.get("relative_volume_20d")), floor=Decimal("0.75"), ceiling=Decimal("2"), maximum=8)),
        "liquidity": _points(_decimal(metrics.get("avg_dollar_volume_20d")), floor=Decimal("30000000"), ceiling=Decimal("300000000"), maximum=10),
        "setup_quality": _points(abs(_decimal(metrics.get("drawdown_12w_high_pct")) or Decimal("100")), floor=Decimal("10"), ceiling=Decimal("55"), maximum=15),
        "trigger_proximity": _points(max(Decimal("0"), Decimal("8") - (distance if distance is not None else Decimal("8"))), floor=Decimal("0"), ceiling=Decimal("8"), maximum=15),
        "risk_reward": 0 if not target_details else min(10, _points(_decimal(target_details[0]["r_multiple"]), floor=Decimal("1"), ceiling=Decimal("4"), maximum=10)),
        "qualitative_confidence": 10 if research else (6 if plan.get("evidence_state") == "reused_current" else 0),
    }
    score = max(0, min(100, sum(components.values()) - min(10, len(snapshot.get("deterministic_risk_flags") or []) * 5)))
    risk_flags = list(snapshot.get("deterministic_risk_flags") or [])
    if research:
        risk_flags.extend(getattr(research, "qualitative_blockers", []) or [])
    return {
        "setup_score": score,
        "score_components": components,
        "structural_targets": target_details,
        "primary_risk": risk_flags[0].replace("_", " ") if risk_flags else None,
    }


def report_focus_sort_key(candidate: dict[str, Any]) -> tuple[int, Decimal, str]:
    score = int(candidate.get("setup_score") or 0)
    distance = abs(_decimal(candidate.get("distance_to_trigger_pct")) or Decimal("999999"))
    return -score, distance, str(candidate.get("ticker") or "")


def _screen_blockers(metrics: dict[str, Any]) -> list[dict[str, str]]:
    """Explain every mechanical screen failure using the configured threshold."""
    definitions = (
        ("revenue_ttm_yoy_pct", "TTM revenue growth", ">=", "minimum_ttm_revenue_growth_pct", _percent),
        ("latest_quarter_revenue_yoy_pct", "latest-quarter revenue growth", ">=", "minimum_quarter_revenue_growth_pct", _percent),
        ("price_change_12w_pct", "12-week price change", "<=", "maximum_price_change_12w_pct", _percent),
        ("avg_dollar_volume_20d", "20-day average dollar volume", ">=", "minimum_avg_dollar_volume_20d", _money),
    )
    blockers = []
    for field, label, operator, threshold_key, formatter in definitions:
        actual = _decimal(metrics.get(field))
        threshold = _decimal(_HARD_THRESHOLDS[threshold_key])
        passed = actual is not None and (
            actual >= threshold if operator == ">=" else actual <= threshold
        )
        if not passed:
            actual_text = formatter(actual) if actual is not None else "not available"
            threshold_text = formatter(threshold)
            blockers.append({
                "gate": f"Screen: {label}",
                "reason": f"{label} is {actual_text}; required {operator} {threshold_text}.",
                "clear_condition": f"{label} must be {operator} {threshold_text}.",
            })
    return blockers


def candidate_presentation(candidate: dict[str, Any]) -> dict[str, Any]:
    """Build the one structured, renderer-ready explanation from canonical state."""
    metrics = candidate.get("metrics") or {}
    positive_factors = []
    rs = _decimal(metrics.get("relative_return_20d_vs_qqq_pct"))
    if rs is not None and rs > 0:
        positive_factors.append(f"{_percent(rs)} 20-day relative strength vs QQQ")
    growth = _decimal(metrics.get("latest_quarter_revenue_yoy_pct"))
    if growth is not None and growth > 0:
        positive_factors.append(f"{_percent(growth)} latest-quarter revenue growth")
    ttm_growth = _decimal(metrics.get("revenue_ttm_yoy_pct"))
    if ttm_growth is not None and ttm_growth > 0:
        positive_factors.append(f"{_percent(ttm_growth)} TTM revenue growth")
    volume = _decimal(metrics.get("avg_dollar_volume_20d"))
    if volume is not None:
        positive_factors.append(f"{_money(volume)} 20-day average dollar volume")

    blockers = _screen_blockers(metrics)
    technical_gate = bool(candidate.get("technical_gate_passed"))
    trigger = candidate.get("trigger_price")
    stop = candidate.get("invalidation_price")
    if not technical_gate:
        if trigger is not None:
            blockers.append({
                "gate": "Technical confirmation",
                "reason": f"Price has not confirmed the {_money(trigger)} breakout trigger.",
                "clear_condition": f"After eligibility requirements are satisfied, close above {_money(trigger)}.",
            })
        else:
            blockers.append({
                "gate": "Technical confirmation",
                "reason": "No valid breakout trigger is available.",
                "clear_condition": "Establish a valid 20-day breakout trigger.",
            })
    if not bool(candidate.get("market_regime_gate_passed")):
        blockers.append({
            "gate": "Market regime",
            "reason": "The market-regime gate is BLOCKED.",
            "clear_condition": "The market-regime gate must pass.",
        })
    payload = candidate.get("payload") or {}
    evidence_status = payload.get("qualitative_evidence_status")
    if evidence_status != "fresh_researched":
        blockers.append({
            "gate": "Qualitative confirmation",
            "reason": "Current qualitative confirmation is not complete.",
            "clear_condition": "Complete current qualitative confirmation.",
        })
    risk_clear_conditions = {
        "unknown_sic_exclusion_status": "A current SIC classification must be available and pass the exclusion screen.",
        "cash_runway_unavailable": "Current source data must show at least 12 months of cash runway or non-negative free cash flow.",
        "cash_runway_below_12_months": "Cash runway must be at least 12 months.",
        "share_count_growth_at_or_above_15_pct": "Year-over-year share-count growth must be below 15.0%.",
    }
    for flag in candidate.get("deterministic_risk_flags") or []:
        blockers.append({
            "gate": "Deterministic risk review",
            "reason": str(flag).replace("_", " ") + ".",
            "clear_condition": risk_clear_conditions.get(
                str(flag), "Resolve the deterministic risk review finding with current source data."
            ),
        })
    for blocker in payload.get("qualitative_blockers") or []:
        blockers.append({
            "gate": "Qualitative review",
            "reason": str(blocker).rstrip(".") + ".",
            "clear_condition": f"Current evidence must show this is resolved: {str(blocker).rstrip('.')}.",
        })
    if candidate.get("primary_risk"):
        blockers.append({
            "gate": "Risk review",
            "reason": str(candidate["primary_risk"]).rstrip(".") + ".",
            "clear_condition": "Resolve or explicitly accept this risk with current source evidence.",
        })

    status = str(candidate.get("buyability_status") or "NOT_ELIGIBLE").upper()
    if status == "NOT_ELIGIBLE":
        classification = "NOT_ELIGIBLE: " + (blockers[0]["reason"] if blockers else "one or more eligibility requirements are not met.")
    elif status == "BUY_NOW":
        classification = "BUY_NOW: all represented eligibility, technical, market, and current qualitative gates pass."
    elif status == "ALMOST_READY":
        classification = "ALMOST_READY: exactly one represented gate remains before eligibility."
    else:
        classification = "RADAR: the setup remains watchable, but eligibility requirements are still incomplete."
    return {
        "group": "watch_first" if status in {"BUY_NOW", "ALMOST_READY", "RADAR"} else "excluded_worth_reviewing",
        "classification": classification,
        "positive_factors": positive_factors or ["No positive deterministic factors are currently available."],
        "blockers": blockers,
        "technical_trigger": (
            f"After eligibility requirements are satisfied, close above {_money(trigger)}."
            if trigger is not None else "Establish a valid 20-day breakout trigger after eligibility requirements are satisfied."
        ),
        "invalidation_level": _money(stop) if stop is not None else "No valid invalidation level is available.",
    }


def ticker_specific_explanation(candidate: dict[str, Any]) -> tuple[str, str]:
    """Compatibility view of the canonical structured presentation model."""
    presentation = candidate_presentation(candidate)
    why = presentation["classification"] + " Positive factors: " + "; ".join(presentation["positive_factors"])
    if presentation["blockers"]:
        why += " Blocking factors: " + "; ".join(
            item["reason"] for item in presentation["blockers"]
        )
    return why, presentation["technical_trigger"]


def presentation_groups(candidates: list[dict[str, Any]], detailed_tickers: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Select the same candidate groups for every destination from canonical data."""
    detailed = set(detailed_tickers)
    selected = [item for item in candidates if item["ticker"] in detailed]
    groups = {"watch_first": [], "excluded_worth_reviewing": []}
    for item in selected:
        presentation = (item.get("payload") or {}).get("presentation") or candidate_presentation(item)
        groups[presentation["group"]].append(item)
    for items in groups.values():
        items.sort(key=report_focus_sort_key)
    return groups
