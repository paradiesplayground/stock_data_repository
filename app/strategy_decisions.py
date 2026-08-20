from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

DECISION_STATUSES = {
    "BUY_SETUP",
    "CONFIRMED_WAIT_FOR_ENTRY",
    "NEAR_TRIGGER",
    "WATCH",
    "AVOID",
    "INVALIDATED",
    "RESEARCH",
}

DECISION_CONTRACT_VERSION = "0.7"

SCREEN_BUCKETS = {
    "qualified",
    "speculative",
    "cooldown",
    "rejected",
    "dropped",
    "incomplete",
}

TECHNICAL_STATES = {
    "no_setup",
    "developing",
    "near_trigger",
    "confirmed",
    "extended",
    "invalidated",
    "unknown",
}

DECISION_PRIORITY = {
    "BUY_SETUP": 0,
    "CONFIRMED_WAIT_FOR_ENTRY": 1,
    "NEAR_TRIGGER": 2,
    "WATCH": 3,
    "RESEARCH": 4,
    "AVOID": 5,
    "INVALIDATED": 6,
}

DECISION_STATUS_DEFINITIONS = {
    "BUY_SETUP": "Tradeable now: every required entry, reward/risk, technical, extension, and market-regime gate passes.",
    "CONFIRMED_WAIT_FOR_ENTRY": "Technically confirmed, but at least one entry-quality gate is not currently acceptable; wait rather than chase.",
    "NEAR_TRIGGER": "Setup is close to the technical entry trigger but confirmation is not complete.",
    "WATCH": "Valid developing candidate that is not yet close enough to an entry trigger to treat as imminent.",
    "RESEARCH": "Potential setup cannot be classified confidently until specified evidence or data is verified.",
    "AVOID": "Known risk/reward or quality issue makes the setup unattractive under the current thesis.",
    "INVALIDATED": "The prior setup or thesis has broken its stated invalidation condition and is no longer eligible without a new thesis.",
}

BUY_SETUP_MIN_T1_R = Decimal("1.00")
BUY_SETUP_MIN_T2_R = Decimal("1.75")
BUY_SETUP_MAX_PCT_ABOVE_TRIGGER = Decimal("5.00")
CALCULATION_TOLERANCE = Decimal("0.02")


class StrategyCandidateDecision(Base):
    __tablename__ = "strategy_candidate_decisions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "ticker",
            name="uq_strategy_candidate_decision_run_ticker",
        ),
        {"schema": "strategy_tracking"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_tracking.strategy_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(32))
    decision_status: Mapped[str] = mapped_column(String(32), index=True)
    status_reason: Mapped[str] = mapped_column(Text)
    next_condition: Mapped[str] = mapped_column(Text)
    technical_state: Mapped[str | None] = mapped_column(String(32), index=True)
    current_entry: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    pct_above_trigger: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    t1_r: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    t2_r: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    technical_gate_passed: Mapped[bool | None] = mapped_column(Boolean)
    market_regime_gate_passed: Mapped[bool | None] = mapped_column(Boolean)


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required for v0.7 decision-status candidates")
    return normalized


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{field} must be numeric") from error


def _choice(value: Any, field: str, choices: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in choices:
        raise ValueError(f"{field} must be one of: " + ", ".join(sorted(choices)))
    return normalized


def _required_decimal(value: Any, field: str) -> Decimal:
    parsed = _decimal(value, field)
    if parsed is None:
        raise ValueError(f"{field} is required for v0.7 decision-status candidates")
    return parsed


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} is required for v0.7 decision-status candidates")
    return value


def _decimal_list(value: Any, field: str, minimum: int = 2) -> list[Decimal]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{field} requires at least {minimum} numeric values")
    return [_required_decimal(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _require_close(actual: Decimal, expected: Decimal, field: str) -> None:
    if abs(actual - expected) > CALCULATION_TOLERANCE:
        raise ValueError(f"{field} is inconsistent with the canonical trade-plan calculation")


def _validate_contract_evidence(
    item: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    ticker = str(item.get("ticker") or "<unknown>")
    status = decision["decision_status"]
    bucket = str(item.get("screen_bucket") or "").strip().lower()
    metrics = _mapping(item.get("metrics"), f"{ticker}.metrics")
    current_price = _required_decimal(metrics.get("close"), f"{ticker}.metrics.close")
    relative_strength = _required_decimal(
        metrics.get("relative_return_20d_vs_qqq_pct"),
        f"{ticker}.metrics.relative_return_20d_vs_qqq_pct",
    )
    relative_volume = _required_decimal(
        metrics.get("relative_volume_20d"),
        f"{ticker}.metrics.relative_volume_20d",
    )
    if current_price <= 0:
        raise ValueError(f"{ticker}.metrics.close must be greater than zero")
    if relative_volume < 0:
        raise ValueError(f"{ticker}.metrics.relative_volume_20d must not be negative")

    for gate in ("technical_gate_passed", "market_regime_gate_passed"):
        if decision[gate] is None:
            raise ValueError(f"{ticker}.{gate} is required for v0.7 candidates")

    if status == "RESEARCH" and bucket != "incomplete":
        raise ValueError(f"{ticker}.RESEARCH requires screen_bucket=incomplete")
    if status in {"AVOID", "INVALIDATED"} and bucket not in {"rejected", "dropped"}:
        raise ValueError(f"{ticker}.{status} requires a rejected or dropped screen_bucket")
    if status in {"BUY_SETUP", "CONFIRMED_WAIT_FOR_ENTRY", "NEAR_TRIGGER", "WATCH"} and bucket not in {
        "qualified",
        "speculative",
        "cooldown",
    }:
        raise ValueError(f"{ticker}.{status} requires a qualified, speculative, or cooldown screen_bucket")

    trade_plan_value = item.get("trade_plan")
    plan_required = status in {"BUY_SETUP", "CONFIRMED_WAIT_FOR_ENTRY", "NEAR_TRIGGER"}
    if trade_plan_value is None and not plan_required:
        return
    plan = _mapping(trade_plan_value, f"{ticker}.trade_plan")
    entry = _required_decimal(plan.get("entry"), f"{ticker}.trade_plan.entry")
    trigger = _required_decimal(plan.get("trigger"), f"{ticker}.trade_plan.trigger")
    stop = _required_decimal(plan.get("stop"), f"{ticker}.trade_plan.stop")
    required_volume = _required_decimal(
        plan.get("required_volume"), f"{ticker}.trade_plan.required_volume"
    )
    whole_shares = _required_decimal(
        plan.get("whole_shares"), f"{ticker}.trade_plan.whole_shares"
    )
    planned_risk = _required_decimal(
        plan.get("planned_risk"), f"{ticker}.trade_plan.planned_risk"
    )
    targets = _decimal_list(plan.get("targets"), f"{ticker}.trade_plan.targets")
    r_multiples = _decimal_list(
        plan.get("r_multiples"), f"{ticker}.trade_plan.r_multiples"
    )
    potential_rewards = _decimal_list(
        plan.get("potential_rewards"), f"{ticker}.trade_plan.potential_rewards"
    )
    current_entry = decision["current_entry"]
    pct_above_trigger = decision["pct_above_trigger"]
    if current_entry is None or pct_above_trigger is None:
        raise ValueError(f"{ticker} trade-plan candidates require current_entry and pct_above_trigger")
    if decision["t1_r"] is None or decision["t2_r"] is None:
        raise ValueError(f"{ticker} trade-plan candidates require t1_r and t2_r")
    if entry <= stop or trigger <= stop:
        raise ValueError(f"{ticker}.trade_plan stop must be below entry and trigger")
    if required_volume <= 0 or whole_shares <= 0:
        raise ValueError(f"{ticker}.trade_plan required_volume and whole_shares must be positive")
    _require_close(current_entry, entry, f"{ticker}.current_entry")
    _require_close(entry, trigger, f"{ticker}.trade_plan.entry")

    risk_per_share = entry - stop
    expected_pct = ((current_price - trigger) / trigger) * Decimal("100")
    _require_close(pct_above_trigger, expected_pct, f"{ticker}.pct_above_trigger")
    _require_close(planned_risk, risk_per_share * whole_shares, f"{ticker}.trade_plan.planned_risk")
    for index in range(2):
        expected_r = (targets[index] - entry) / risk_per_share
        expected_reward = (targets[index] - entry) * whole_shares
        _require_close(r_multiples[index], expected_r, f"{ticker}.trade_plan.r_multiples[{index}]")
        _require_close(potential_rewards[index], expected_reward, f"{ticker}.trade_plan.potential_rewards[{index}]")
    _require_close(decision["t1_r"], r_multiples[0], f"{ticker}.t1_r")
    _require_close(decision["t2_r"], r_multiples[1], f"{ticker}.t2_r")

    if status == "BUY_SETUP":
        if pct_above_trigger < 0 or current_price < trigger:
            raise ValueError(f"{ticker}.BUY_SETUP current price must be at or above the trigger")
        if current_price > trigger * Decimal("1.05"):
            raise ValueError(f"{ticker}.BUY_SETUP current price is above the allowed entry zone")
        if relative_strength <= 0:
            raise ValueError(f"{ticker}.BUY_SETUP requires positive 20D relative strength vs QQQ")
        if relative_volume < required_volume:
            raise ValueError(f"{ticker}.BUY_SETUP requires relative volume >= required_volume")
    elif status == "CONFIRMED_WAIT_FOR_ENTRY":
        if decision["technical_state"] != "confirmed" or decision["technical_gate_passed"] is not True:
            raise ValueError(f"{ticker}.CONFIRMED_WAIT_FOR_ENTRY requires confirmed technical state")
        ready = (
            decision["market_regime_gate_passed"] is True
            and relative_strength > 0
            and relative_volume >= required_volume
            and Decimal("0") <= pct_above_trigger <= BUY_SETUP_MAX_PCT_ABOVE_TRIGGER
            and decision["t1_r"] >= BUY_SETUP_MIN_T1_R
            and decision["t2_r"] >= BUY_SETUP_MIN_T2_R
        )
        if ready:
            raise ValueError(f"{ticker}.CONFIRMED_WAIT_FOR_ENTRY has no failing entry-quality gate")
    elif status == "NEAR_TRIGGER" and decision["technical_gate_passed"] is not False:
        raise ValueError(f"{ticker}.NEAR_TRIGGER requires technical_gate_passed=false")


def normalize_candidate_decision(
    item: dict[str, Any],
    *,
    contract_required: bool = False,
) -> dict[str, Any] | None:
    raw_status = item.get("decision_status")
    has_decision_fields = raw_status is not None or any(
        key in item
        for key in (
            "status_reason",
            "next_condition",
            "current_entry",
            "pct_above_trigger",
            "t1_r",
            "t2_r",
            "technical_gate_passed",
            "market_regime_gate_passed",
        )
    )
    if not has_decision_fields and not contract_required:
        return None

    if contract_required:
        _choice(item.get("screen_bucket"), "screen_bucket", SCREEN_BUCKETS)

    status = str(raw_status or "").strip().upper()
    if status not in DECISION_STATUSES:
        raise ValueError(
            "decision_status must be one of: " + ", ".join(sorted(DECISION_STATUSES))
        )

    decision = {
        "decision_status": status,
        "status_reason": _required_text(item.get("status_reason"), "status_reason"),
        "next_condition": _required_text(item.get("next_condition"), "next_condition"),
        "technical_state": _choice(
            item.get("technical_state"), "technical_state", TECHNICAL_STATES
        )
        if contract_required or item.get("technical_state") is not None
        else None,
        "current_entry": _decimal(item.get("current_entry"), "current_entry"),
        "pct_above_trigger": _decimal(
            item.get("pct_above_trigger"), "pct_above_trigger"
        ),
        "t1_r": _decimal(item.get("t1_r"), "t1_r"),
        "t2_r": _decimal(item.get("t2_r"), "t2_r"),
        "technical_gate_passed": item.get("technical_gate_passed"),
        "market_regime_gate_passed": item.get("market_regime_gate_passed"),
    }

    for gate in ("technical_gate_passed", "market_regime_gate_passed"):
        if decision[gate] is not None and not isinstance(decision[gate], bool):
            raise ValueError(f"{gate} must be true, false, or null")

    if status == "BUY_SETUP":
        missing = [
            field
            for field in (
                "pct_above_trigger",
                "t1_r",
                "t2_r",
                "technical_gate_passed",
                "market_regime_gate_passed",
            )
            if decision[field] is None
        ]
        if missing:
            raise ValueError(
                "BUY_SETUP requires explicit entry-quality gates: " + ", ".join(missing)
            )
        if decision["t1_r"] < BUY_SETUP_MIN_T1_R:
            raise ValueError("BUY_SETUP requires t1_r >= 1.00")
        if decision["t2_r"] < BUY_SETUP_MIN_T2_R:
            raise ValueError("BUY_SETUP requires t2_r >= 1.75")
        if decision["pct_above_trigger"] > BUY_SETUP_MAX_PCT_ABOVE_TRIGGER:
            raise ValueError("BUY_SETUP requires pct_above_trigger <= 5.00")
        if decision["technical_gate_passed"] is not True:
            raise ValueError("BUY_SETUP requires technical_gate_passed=true")
        if decision["market_regime_gate_passed"] is not True:
            raise ValueError("BUY_SETUP requires market_regime_gate_passed=true")
        if decision["technical_state"] not in (None, "confirmed"):
            raise ValueError("BUY_SETUP requires technical_state=confirmed")

    if status == "NEAR_TRIGGER" and decision["technical_state"] not in (
        None,
        "near_trigger",
    ):
        raise ValueError("NEAR_TRIGGER requires technical_state=near_trigger")

    if status == "INVALIDATED" and decision["technical_state"] not in (
        None,
        "invalidated",
    ):
        raise ValueError("INVALIDATED requires technical_state=invalidated")

    if contract_required:
        _validate_contract_evidence(item, decision)

    return decision


def decision_sort_key(
    status: str | None,
    score: Decimal | None,
    ticker: str,
) -> tuple[int, Decimal, str]:
    priority = DECISION_PRIORITY.get(status or "", len(DECISION_PRIORITY) + 1)
    return priority, -(score or Decimal("-999999")), ticker
