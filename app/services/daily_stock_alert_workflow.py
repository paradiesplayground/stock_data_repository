"""Durable checkpoints and server-side assembly for the daily alert workflow."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import DailyAlertPreparation, DailyAlertPreparationResearch
from app.services.daily_stock_alert import run_daily_stock_alert, validate_daily_stock_alert


def _supports_persistence(session: Any) -> bool:
    return all(hasattr(session, method) for method in ("scalar", "add", "commit", "flush"))


def persist_preparation(session: Session, prepared: dict[str, Any]) -> dict[str, Any]:
    """Create exactly one checkpoint for a deterministic snapshot/idempotency key."""
    if not _supports_persistence(session):  # Unit-level deterministic preparation tests.
        prepared["preparation_id"] = "transient-" + prepared["run_template"]["idempotency_key"]
        return prepared
    key = prepared["run_template"]["idempotency_key"]
    existing = session.scalar(
        select(DailyAlertPreparation).where(DailyAlertPreparation.preparation_key == key)
    )
    if existing:
        result = deepcopy(existing.snapshot)
        result["preparation_id"] = existing.preparation_id
        result["preparation_reused"] = True
        return result
    preparation_id = str(uuid4())
    snapshot = deepcopy(prepared)
    snapshot["preparation_id"] = preparation_id
    session.add(DailyAlertPreparation(
        preparation_id=preparation_id,
        preparation_key=key,
        as_of_date=prepared["as_of_date"],
        strategy_key=prepared["strategy_key"],
        strategy_version=prepared["strategy_version"],
        snapshot=snapshot,
    ))
    session.flush()
    session.commit()
    snapshot["preparation_reused"] = False
    return snapshot


def _preparation(session: Session, preparation_id: str) -> DailyAlertPreparation:
    item = session.scalar(
        select(DailyAlertPreparation).where(DailyAlertPreparation.preparation_id == preparation_id)
    )
    if not item:
        raise ValueError("unknown daily alert preparation_id")
    return item


def _research_by_ticker(session: Session, preparation_id: str) -> dict[str, DailyAlertPreparationResearch]:
    items = session.scalars(
        select(DailyAlertPreparationResearch).where(
            DailyAlertPreparationResearch.preparation_id == preparation_id
        )
    ).all()
    return {item.ticker: item for item in items}


def record_daily_stock_alert_research(
    session: Session,
    *,
    preparation_id: str,
    ticker: str,
    evidence: list[dict[str, Any]],
    required_dimensions: dict[str, bool],
    qualitative_blockers: list[str] | None = None,
    qualitative_flags: list[str] | None = None,
    candidate_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Idempotently checkpoint one completed deep-research result."""
    preparation = _preparation(session, preparation_id)
    normalized_ticker = ticker.strip().upper()
    deep_tickers = {item["ticker"] for item in preparation.snapshot["deep_research_queue"]}
    if normalized_ticker not in deep_tickers:
        raise ValueError("research checkpoints are allowed only for deep_research_queue tickers")
    required = set(
        next(item["qualitative_evidence_required"] for item in preparation.snapshot["deep_research_queue"] if item["ticker"] == normalized_ticker)
    )
    missing = sorted(dimension for dimension in required if not required_dimensions.get(dimension))
    if missing:
        raise ValueError("research checkpoint is incomplete: " + ", ".join(missing))
    existing = session.scalar(select(DailyAlertPreparationResearch).where(
        DailyAlertPreparationResearch.preparation_id == preparation_id,
        DailyAlertPreparationResearch.ticker == normalized_ticker,
    ))
    values = {
        "evidence": deepcopy(evidence), "required_dimensions": dict(required_dimensions),
        "qualitative_blockers": sorted(set(qualitative_blockers or [])),
        "qualitative_flags": sorted(set(qualitative_flags or [])),
        "candidate_decision": deepcopy(candidate_decision),
    }
    if existing:
        for field, value in values.items():
            setattr(existing, field, value)
        reused = True
    else:
        session.add(DailyAlertPreparationResearch(
            preparation_id=preparation_id, ticker=normalized_ticker, **values
        ))
        reused = False
    session.commit()
    return {"status": "research_recorded", "preparation_id": preparation_id, "ticker": normalized_ticker, "idempotent_update": reused}


def daily_stock_alert_preparation_status(session: Session, *, preparation_id: str) -> dict[str, Any]:
    preparation = _preparation(session, preparation_id)
    snapshot = preparation.snapshot
    completed = sorted(_research_by_ticker(session, preparation_id))
    deep = sorted(item["ticker"] for item in snapshot["deep_research_queue"])
    return {
        "preparation_id": preparation_id,
        "as_of_date": snapshot["as_of_date"],
        "expected_ticker_count": len(snapshot["run_template"]["summary"]["preparation_scope"]["expected_candidate_tickers"]),
        "completed_deep_research_tickers": completed,
        "outstanding_deep_research_tickers": sorted(set(deep) - set(completed)),
        "finalization_complete": preparation.final_payload is not None,
        "validation_status": (preparation.validation or {}).get("status", "not_validated"),
        "production_status": "completed" if preparation.production_run_id else "not_run",
        "production_run_id": preparation.production_run_id,
    }


def _default_candidate(snapshot: dict[str, Any], plan: dict[str, Any], research: DailyAlertPreparationResearch | None) -> dict[str, Any]:
    metrics = dict(snapshot["deterministic_metrics"])
    metrics.setdefault("relative_volume_20d", "0")
    gates = snapshot["represented_gates"]
    dropped = "dropped_from_raw_pool" in plan["reasons"]
    rejected = dropped or bool(snapshot["deterministic_risk_flags"])
    technical = bool(gates["price_at_or_above_trigger"])
    distance = snapshot.get("distance_to_trigger_pct")
    represented_failures = (
        int(not technical)
        + int(not gates["market_regime_gate_passed"])
        + int(distance is not None and Decimal(str(distance)) > 0)
    )
    status = "NOT_ELIGIBLE" if rejected else "RADAR"
    base = {
        "ticker": snapshot["ticker"],
        "screen_bucket": "dropped" if dropped else ("rejected" if rejected else "qualified"),
        "technical_state": "confirmed" if technical else "developing",
        "buyability_status": status,
        "status_reason": "Deterministic-only classification; current qualitative confirmation is required before an actionable status." if not research else "Finalized from saved qualitative research.",
        "buy_conditions": ["Complete current qualitative confirmation before any actionable decision."],
        "remaining_gate_count": max(2, represented_failures),
        "current_price": metrics.get("close"),
        "trigger_price": snapshot.get("suggested_trigger_price"),
        "distance_to_trigger_pct": distance,
        "invalidation_price": snapshot.get("suggested_invalidation_price"),
        "technical_gate_passed": technical,
        "market_regime_gate_passed": gates["market_regime_gate_passed"],
        "metrics": metrics,
        "payload": {
            "in_raw_pool": not dropped,
            "qualitative_evidence_status": "fresh_researched" if research else plan["evidence_state"],
            "qualitative_blockers": research.qualitative_blockers if research else [],
            "qualitative_flags": research.qualitative_flags if research else [],
        },
    }
    if research and research.candidate_decision:
        # A per-ticker decision is checkpointed, not a caller-assembled run payload.
        base.update(deepcopy(research.candidate_decision))
        base["payload"] = {**base["payload"], **(research.candidate_decision.get("payload") or {}), "qualitative_evidence_status": "fresh_researched"}
    if base["buyability_status"] in {"BUY_NOW", "ALMOST_READY"} and not research:
        raise ValueError(f"{base['ticker']} cannot become actionable without saved qualitative research")
    return base


def finalize_daily_stock_alert_preparation(session: Session, settings: Settings, *, preparation_id: str) -> dict[str, Any]:
    """Assemble, validate, and save the exact canonical payload server-side."""
    preparation = _preparation(session, preparation_id)
    if preparation.final_payload is not None:
        return {"status": "finalized", "preparation_id": preparation_id, "validated": preparation.validation, "run_payload": preparation.final_payload, "idempotent_replay": True}
    snapshot = preparation.snapshot
    research = _research_by_ticker(session, preparation_id)
    deep = {item["ticker"] for item in snapshot["deep_research_queue"]}
    missing = sorted(deep - set(research))
    if missing:
        raise ValueError("outstanding deep research: " + ", ".join(missing))
    plans = {item["ticker"]: item for item in snapshot["all_research_plans"]}
    expected = snapshot["run_template"]["summary"]["preparation_scope"]["expected_candidate_tickers"]
    candidates = [_default_candidate(snapshot["candidate_snapshots"][ticker], plans[ticker], research.get(ticker)) for ticker in expected]
    evidence = []
    for item in snapshot["carry_forward_queue"]:
        evidence.extend(item["reusable_prior_evidence"])
    for item in research.values():
        evidence.extend(item.evidence)
    payload = deepcopy(snapshot["run_template"])
    payload["candidates"] = candidates
    payload["evidence"] = evidence
    payload["summary"] = {**payload["summary"], "preparation_id": preparation_id, "candidate_counts": {status: sum(item["buyability_status"] == status for item in candidates) for status in ("BUY_NOW", "ALMOST_READY", "RADAR", "NOT_ELIGIBLE")}}
    lines = ["# Daily Stock Alert", "", "## Closest setups"]
    for item in candidates:
        if item["ticker"] in snapshot["report_scope"]["detailed_tickers"]:
            lines.append(f"- **{item['ticker']}** — {item['buyability_status']}: {item['status_reason']}")
    compact = len(snapshot["report_scope"]["compact_summary_tickers"])
    if compact:
        lines.append(f"- {compact} additional tracked stocks are retained in the canonical record as a compact summary.")
    payload["report_markdown"] = "\n".join(lines)
    validation = validate_daily_stock_alert(session, settings, as_of_date=snapshot["as_of_date"], run_payload=payload)
    preparation.final_payload = validation["validated_run_payload"]
    preparation.validation = {"status": validation["status"], "payload_hash": validation["payload_hash"]}
    session.commit()
    return {"status": "finalized", "preparation_id": preparation_id, "validated": preparation.validation, "run_payload": preparation.final_payload, "idempotent_replay": False}


def run_finalized_daily_stock_alert_preparation(session: Session, settings: Settings, *, preparation_id: str) -> dict[str, Any]:
    preparation = _preparation(session, preparation_id)
    if preparation.final_payload is None or (preparation.validation or {}).get("status") != "valid":
        raise ValueError("preparation must be finalized and validated before production")
    result = run_daily_stock_alert(session, settings, as_of_date=preparation.snapshot["as_of_date"], run_payload=preparation.final_payload, verify_mailbox=False)
    preparation.production_run_id = result["run_id"]
    session.commit()
    return result
