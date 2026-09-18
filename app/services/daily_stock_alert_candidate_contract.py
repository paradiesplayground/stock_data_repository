"""Canonical candidate state for durable daily-alert finalization.

This module is deliberately pure: finalization supplies saved snapshots and
research, while validation supplies the already-finalized candidate.  Both
paths therefore apply the same actionability, market-veto, bucket, gate-count,
and research-freshness rules.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any


ACTIONABLE_STATUSES = {"BUY_NOW", "ALMOST_READY"}
ELIGIBLE_BUCKETS = {"qualified", "speculative", "cooldown"}


def fresh_research_checkpoint(
    *,
    preparation_id: str,
    ticker: str,
    preparation_created_at_utc: datetime | None,
    research_updated_at_utc: datetime | None,
    is_deep_research: bool,
) -> dict[str, str] | None:
    """Create the server-owned marker that proves current deep research."""
    if (
        not is_deep_research
        or not isinstance(preparation_created_at_utc, datetime)
        or not isinstance(research_updated_at_utc, datetime)
        or preparation_created_at_utc.tzinfo is None
        or research_updated_at_utc.tzinfo is None
        or research_updated_at_utc <= preparation_created_at_utc
    ):
        return None
    return {
        "preparation_id": preparation_id,
        "ticker": ticker,
        "research_scope": "deep_research",
        "completed_at_utc": research_updated_at_utc.isoformat(),
    }


def is_fresh_research_checkpoint(
    checkpoint: Any,
    *,
    ticker: str,
    preparation_id: Any,
    preparation_created_at_utc: Any,
) -> bool:
    """Verify a serialized server-owned freshness marker."""
    if not isinstance(checkpoint, dict):
        return False
    if checkpoint.get("research_scope") != "deep_research":
        return False
    if str(checkpoint.get("ticker") or "").strip().upper() != ticker:
        return False
    if str(checkpoint.get("preparation_id") or "") != str(preparation_id or ""):
        return False
    try:
        completed_at = datetime.fromisoformat(str(checkpoint["completed_at_utc"]).replace("Z", "+00:00"))
        prepared_at = datetime.fromisoformat(str(preparation_created_at_utc).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    return (
        completed_at.tzinfo is not None
        and prepared_at.tzinfo is not None
        and completed_at > prepared_at
    )


def represented_gate_count(candidate: dict[str, Any]) -> int:
    """Count the explicit technical, market, and trigger-distance gates."""
    distance = candidate.get("distance_to_trigger_pct")
    return (
        int(not bool(candidate.get("technical_gate_passed")))
        + int(not bool(candidate.get("market_regime_gate_passed")))
        + int(distance is not None and Decimal(str(distance)) > 0)
    )


def normalize_candidate_state(
    candidate: dict[str, Any],
    *,
    market_regime_gate_passed: bool,
    fresh_checkpoint: dict[str, str] | None,
    require_fresh_research_for_actionable: bool = True,
) -> dict[str, Any]:
    """Apply the one effective v0.8 candidate contract to a setup candidate."""
    normalized = deepcopy(candidate)
    payload = dict(normalized.get("payload") or {})
    setup_bucket = str(normalized.get("screen_bucket") or "").strip().lower()
    setup_status = str(normalized.get("buyability_status") or "").strip().upper()
    setup_remaining = int(normalized.get("remaining_gate_count") or 0)

    if (
        setup_status in ACTIONABLE_STATUSES
        and require_fresh_research_for_actionable
        and not fresh_checkpoint
    ):
        raise ValueError(
            f"{normalized.get('ticker', '<unknown>')} {setup_status} requires fresh qualitative evidence after preparation"
        )
    if setup_bucket not in ELIGIBLE_BUCKETS:
        setup_status = "NOT_ELIGIBLE"

    # v0.8 permits NOT_ELIGIBLE only in a terminal/non-actionable bucket. Keep
    # the saved setup bucket in payload metadata, but map its effective bucket
    # before the shared validator sees the candidate.
    effective_bucket = setup_bucket
    if setup_status == "NOT_ELIGIBLE" and effective_bucket not in {
        "rejected",
        "dropped",
        "incomplete",
    }:
        effective_bucket = "rejected"

    normalized["market_regime_gate_passed"] = bool(market_regime_gate_passed)
    normalized["buyability_status"] = setup_status
    normalized["screen_bucket"] = effective_bucket
    represented_remaining = represented_gate_count(normalized)
    normalized["remaining_gate_count"] = max(setup_remaining, represented_remaining)

    # ALMOST_READY is not a loose label: v0.8 requires precisely one
    # represented gate plus a structured, near-trigger risk plan. Derive that
    # state from the canonical fields rather than trusting a checkpointed count.
    if setup_status == "ALMOST_READY":
        trigger = normalized.get("trigger_price")
        invalidation = normalized.get("invalidation_price")
        distance = normalized.get("distance_to_trigger_pct")
        structured_near_trigger = (
            trigger is not None
            and invalidation is not None
            and distance is not None
            and Decimal("0") <= Decimal(str(distance)) <= Decimal("5")
        )
        if represented_remaining == 1 and structured_near_trigger:
            normalized["remaining_gate_count"] = 1
        else:
            setup_status = "RADAR"
            normalized["buyability_status"] = setup_status
            normalized["remaining_gate_count"] = max(1, represented_remaining)
    payload.update(
        setup_buyability_status=setup_status,
        setup_screen_bucket=setup_bucket,
        setup_remaining_gate_count=setup_remaining,
        setup_status_reason=normalized.get("status_reason"),
        setup_market_regime_gate_passed=bool(market_regime_gate_passed),
        qualitative_evidence_status=("fresh_researched" if fresh_checkpoint else payload.get("qualitative_evidence_status")),
        qualitative_research_checkpoint=fresh_checkpoint,
        market_actionability_status=("MARKET_OPEN" if market_regime_gate_passed else "MARKET_BLOCKED"),
    )

    if not market_regime_gate_passed:
        normalized["buyability_status"] = "NOT_ELIGIBLE"
        if setup_bucket in ELIGIBLE_BUCKETS:
            normalized["screen_bucket"] = "rejected"
        normalized["remaining_gate_count"] = max(
            normalized["remaining_gate_count"], represented_gate_count(normalized)
        )
        normalized["status_reason"] = (
            f"Market regime BLOCK; underlying setup remains {setup_status}. "
            f"{normalized.get('status_reason') or ''}".strip()
        )
        market_condition = "Wait for the market-regime gate to pass before considering an entry."
        normalized["buy_conditions"] = [
            market_condition,
            *[item for item in normalized.get("buy_conditions") or [] if item != market_condition],
        ]
    normalized["payload"] = payload
    return normalized


def validate_finalized_candidate_state(
    candidate: dict[str, Any],
    *,
    preparation_id: Any,
    preparation_created_at_utc: Any,
    require_fresh_research_for_actionable: bool,
) -> None:
    """Reject a submitted candidate that is not the canonical effective state."""
    ticker = str(candidate.get("ticker") or "").strip().upper()
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    setup_status = str(payload.get("setup_buyability_status") or candidate.get("buyability_status") or "").upper()
    setup_bucket = str(payload.get("setup_screen_bucket") or candidate.get("screen_bucket") or "").lower()
    checkpoint = payload.get("qualitative_research_checkpoint")
    fresh = is_fresh_research_checkpoint(
        checkpoint,
        ticker=ticker,
        preparation_id=preparation_id,
        preparation_created_at_utc=preparation_created_at_utc,
    )
    setup = deepcopy(candidate)
    setup.update(
        buyability_status=setup_status,
        screen_bucket=setup_bucket,
        remaining_gate_count=payload.get("setup_remaining_gate_count", candidate.get("remaining_gate_count")),
        status_reason=payload.get("setup_status_reason", candidate.get("status_reason")),
    )
    canonical = normalize_candidate_state(
        setup,
        market_regime_gate_passed=bool(payload.get("setup_market_regime_gate_passed", candidate.get("market_regime_gate_passed"))),
        fresh_checkpoint=checkpoint if fresh else None,
        require_fresh_research_for_actionable=require_fresh_research_for_actionable,
    )
    for field in (
        "buyability_status",
        "screen_bucket",
        "remaining_gate_count",
        "market_regime_gate_passed",
        "status_reason",
        "buy_conditions",
    ):
        if candidate.get(field) != canonical.get(field):
            raise ValueError(f"{ticker} final candidate violates canonical {field} contract")
    for field in (
        "setup_buyability_status",
        "setup_screen_bucket",
        "setup_remaining_gate_count",
        "setup_status_reason",
        "setup_market_regime_gate_passed",
        "market_actionability_status",
        "qualitative_evidence_status",
        "qualitative_research_checkpoint",
    ):
        if payload.get(field) != canonical["payload"].get(field):
            raise ValueError(f"{ticker} final candidate violates canonical payload.{field} contract")
