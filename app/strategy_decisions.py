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

    return decision


def decision_sort_key(
    status: str | None,
    score: Decimal | None,
    ticker: str,
) -> tuple[int, Decimal, str]:
    priority = DECISION_PRIORITY.get(status or "", len(DECISION_PRIORITY) + 1)
    return priority, -(score or Decimal("-999999")), ticker
