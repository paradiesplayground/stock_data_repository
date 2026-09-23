"""Deterministic report enrichment for the daily stock alert."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


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


def ticker_specific_explanation(candidate: dict[str, Any]) -> tuple[str, str]:
    """Use snapshot facts, not workflow-state boilerplate, in user-facing prose."""
    metrics = candidate.get("metrics") or {}
    factors = []
    rs = _decimal(metrics.get("relative_return_20d_vs_qqq_pct"))
    if rs is not None:
        factors.append(f"{_percent(rs)} 20-day relative strength vs QQQ" if rs > 0 else f"{_percent(abs(rs))} 20-day relative weakness vs QQQ")
    growth = _decimal(metrics.get("latest_quarter_revenue_yoy_pct"))
    if growth is not None:
        factors.append(f"{_percent(growth)} latest-quarter revenue growth")
    distance = _decimal(candidate.get("distance_to_trigger_pct"))
    if distance is not None:
        factors.append(f"{_percent(abs(distance))} {'below' if distance >= 0 else 'above'} {_money(candidate.get('trigger_price'))} trigger")
    why = "; ".join(factors[:3]) or "Deterministic screen data is incomplete for a fuller setup explanation"
    risk = candidate.get("primary_risk")
    if risk:
        why += f". Primary risk: {risk}."
    trigger = candidate.get("trigger_price")
    stop = candidate.get("invalidation_price")
    volume = _decimal(metrics.get("relative_volume_20d"))
    if trigger is not None:
        next_condition = f"Close above {_money(trigger)}"
        if volume is not None and volume < 1:
            next_condition += " with relative volume back to at least 1.00x"
        if stop is not None:
            next_condition += f"; invalidate below {_money(stop)}"
        return why, next_condition + "."
    return why, "Establish a valid 20-day breakout trigger and structural stop."
