"""Durable checkpoints and server-side assembly for the daily alert workflow."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from decimal import Decimal
import re
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import DailyAlertPreparation, DailyAlertPreparationResearch
from app.services.daily_stock_alert import run_daily_stock_alert, validate_daily_stock_alert
from app.services.daily_stock_alert_candidate_contract import (
    fresh_research_checkpoint,
    normalize_candidate_state,
)
from app.services.daily_stock_alert_report import (
    candidate_presentation,
    presentation_groups,
    report_enrichment,
)


_PREPARATION_REVISION_REASON: ContextVar[str | None] = ContextVar(
    "daily_alert_preparation_revision_reason",
    default=None,
)
_EVIDENCE_TYPE_INVALID = re.compile(r"[^a-z0-9._-]+")
_EVIDENCE_TYPE_SEPARATORS = re.compile(r"[._-]{2,}")
_EVIDENCE_TYPE_FALLBACK = "qualitative_research"


def _supports_persistence(session: Any) -> bool:
    return all(hasattr(session, method) for method in ("scalar", "add", "commit", "flush"))


def _normalize_evidence_type(value: Any) -> str:
    """Return the canonical strategy-tracking identifier form for evidence types."""
    normalized = _EVIDENCE_TYPE_INVALID.sub("_", str(value or "").strip().lower())
    normalized = _EVIDENCE_TYPE_SEPARATORS.sub("_", normalized).strip("._-")
    normalized = normalized[:64].rstrip("._-")
    return normalized or _EVIDENCE_TYPE_FALLBACK


def _normalize_evidence_record(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize one evidence record while retaining the caller's original label."""
    if not isinstance(item, dict):
        raise ValueError("research evidence records must be objects")
    normalized = deepcopy(item)
    original = str(item.get("evidence_type") or "")
    evidence_type = _normalize_evidence_type(original)
    normalized["evidence_type"] = evidence_type
    if evidence_type != original:
        existing_details = normalized.get("details")
        if isinstance(existing_details, dict):
            details = deepcopy(existing_details)
        elif existing_details is None:
            details = {}
        else:
            details = {"original_details": deepcopy(existing_details)}
        details.setdefault("original_evidence_type", original)
        normalized["details"] = details
    return normalized


def _normalize_evidence_records(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_normalize_evidence_record(item) for item in evidence]


@contextmanager
def new_daily_stock_alert_preparation_revision(reason: str) -> Iterator[None]:
    """Request one fresh durable preparation revision inside the current call context."""
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("a preparation revision requires a non-empty reason")
    token = _PREPARATION_REVISION_REASON.set(normalized_reason)
    try:
        yield
    finally:
        _PREPARATION_REVISION_REASON.reset(token)


def _latest_preparation_for_base_key(
    session: Session,
    base_key: str,
) -> DailyAlertPreparation | None:
    revision_prefix = f"{base_key}:revision:"
    return session.scalar(
        select(DailyAlertPreparation)
        .where(
            or_(
                DailyAlertPreparation.preparation_key == base_key,
                DailyAlertPreparation.preparation_key.like(revision_prefix + "%"),
            )
        )
        .order_by(desc(DailyAlertPreparation.created_at_utc))
        .limit(1)
    )


def persist_preparation(session: Session, prepared: dict[str, Any]) -> dict[str, Any]:
    """Persist or resume the latest checkpoint, with explicit opt-in revisions."""
    base_key = prepared["run_template"]["idempotency_key"]
    revision_reason = _PREPARATION_REVISION_REASON.get()
    if not _supports_persistence(session):  # Unit-level deterministic preparation tests.
        revision = 1 if revision_reason else 0
        key = f"{base_key}:revision:{revision}" if revision else base_key
        snapshot = deepcopy(prepared)
        snapshot["run_template"] = deepcopy(snapshot["run_template"])
        snapshot["run_template"]["idempotency_key"] = key
        snapshot["preparation_base_key"] = base_key
        snapshot["preparation_revision"] = revision
        if revision_reason:
            snapshot["preparation_revision_reason"] = revision_reason
        snapshot["preparation_id"] = "transient-" + key
        snapshot["preparation_reused"] = False
        return snapshot

    latest = _latest_preparation_for_base_key(session, base_key)
    if latest and not revision_reason:
        result = deepcopy(latest.snapshot)
        result["preparation_id"] = latest.preparation_id
        result["preparation_reused"] = True
        return result

    latest_revision = int((latest.snapshot or {}).get("preparation_revision") or 0) if latest else 0
    revision = latest_revision + 1 if revision_reason else 0
    key = f"{base_key}:revision:{revision}" if revision else base_key
    if len(key) > 255:
        raise ValueError("daily alert preparation revision key exceeds 255 characters")

    preparation_id = str(uuid4())
    snapshot = deepcopy(prepared)
    snapshot["run_template"] = deepcopy(snapshot["run_template"])
    snapshot["run_template"]["idempotency_key"] = key
    snapshot["preparation_base_key"] = base_key
    snapshot["preparation_revision"] = revision
    if revision_reason:
        snapshot["preparation_revision_reason"] = revision_reason
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
        "evidence": _normalize_evidence_records(evidence), "required_dimensions": dict(required_dimensions),
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
        "preparation_revision": int(snapshot.get("preparation_revision") or 0),
        "preparation_revision_reason": snapshot.get("preparation_revision_reason"),
        "as_of_date": snapshot["as_of_date"],
        "expected_ticker_count": len(snapshot["run_template"]["summary"]["preparation_scope"]["expected_candidate_tickers"]),
        "completed_deep_research_tickers": completed,
        "outstanding_deep_research_tickers": sorted(set(deep) - set(completed)),
        "finalization_complete": preparation.final_payload is not None,
        "validation_status": (preparation.validation or {}).get("status", "not_validated"),
        "production_status": "completed" if preparation.production_run_id else "not_run",
        "production_run_id": preparation.production_run_id,
    }


def _default_candidate(
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    research: DailyAlertPreparationResearch | None,
    fresh_research_checkpoint: dict[str, str] | None = None,
) -> dict[str, Any]:
    metrics = dict(snapshot["deterministic_metrics"])
    metrics.setdefault("relative_volume_20d", "0")
    gates = snapshot["represented_gates"]
    dropped = "dropped_from_raw_pool" in plan["reasons"]
    rejected = dropped or bool(snapshot["deterministic_risk_flags"])
    technical = bool(gates["price_at_or_above_trigger"])
    distance = snapshot.get("distance_to_trigger_pct")
    status = "NOT_ELIGIBLE" if rejected else "RADAR"
    default_buy_condition = (
        f"Close above ${Decimal(str(snapshot['suggested_trigger_price'])):.2f} after all eligibility requirements pass."
        if research and snapshot.get("suggested_trigger_price") is not None
        else "Establish a valid technical trigger after all eligibility requirements pass."
    )
    base = {
        "ticker": snapshot["ticker"],
        "screen_bucket": "dropped" if dropped else ("rejected" if rejected else "qualified"),
        "technical_state": "confirmed" if technical else "developing",
        "buyability_status": status,
        "status_reason": "Deterministic-only classification; current qualitative confirmation is required before an actionable status." if not research else "Finalized from saved qualitative research.",
        "buy_conditions": [
            default_buy_condition if research else
            "Complete current qualitative confirmation before any actionable decision."
        ],
        "remaining_gate_count": 0,
        "current_price": metrics.get("close"),
        "trigger_price": snapshot.get("suggested_trigger_price"),
        "distance_to_trigger_pct": distance,
        "invalidation_price": snapshot.get("suggested_invalidation_price"),
        "technical_gate_passed": technical,
        "market_regime_gate_passed": gates["market_regime_gate_passed"],
        "metrics": metrics,
        "deterministic_risk_flags": list(snapshot.get("deterministic_risk_flags") or []),
        "payload": {
            "in_raw_pool": not dropped,
            "qualitative_evidence_status": (
                "fresh_researched" if fresh_research_checkpoint else plan["evidence_state"]
            ),
            "qualitative_research_checkpoint": fresh_research_checkpoint,
            "qualitative_blockers": research.qualitative_blockers if research else [],
            "qualitative_flags": research.qualitative_flags if research else [],
        },
    }
    if research and research.candidate_decision:
        # A per-ticker decision is checkpointed, not a caller-assembled run payload.
        base.update(deepcopy(research.candidate_decision))
        base["payload"] = {
            **base["payload"],
            **(research.candidate_decision.get("payload") or {}),
            "qualitative_evidence_status": (
                "fresh_researched" if fresh_research_checkpoint else "stale_researched"
            ),
            "qualitative_research_checkpoint": fresh_research_checkpoint,
        }

    # All finalization rules are shared with validation; do not derive effective
    # actionability from raw fields anywhere else in this workflow.
    normalized = normalize_candidate_state(
        base,
        market_regime_gate_passed=bool(gates["market_regime_gate_passed"]),
        fresh_checkpoint=fresh_research_checkpoint,
    )
    normalized.update(report_enrichment(snapshot=snapshot, plan=plan, research=research))
    presentation = candidate_presentation(normalized)
    # Presentation is canonical structured data consumed by the shared report;
    # it is never inferred separately by delivery destinations.
    normalized["payload"] = {
        **(normalized.get("payload") or {}),
        "presentation": presentation,
    }
    return normalized


def _setup_sort_key(candidate: dict[str, Any]) -> tuple[int, int, Decimal, str]:
    payload = candidate.get("payload") or {}
    status = str(
        payload.get("setup_buyability_status") or candidate["buyability_status"]
    ).upper()
    priority = {"BUY_NOW": 0, "ALMOST_READY": 1, "RADAR": 2, "NOT_ELIGIBLE": 3}
    remaining = int(
        payload.get("setup_remaining_gate_count", candidate.get("remaining_gate_count") or 0)
    )
    distance_value = candidate.get("distance_to_trigger_pct")
    distance = abs(Decimal(str(distance_value))) if distance_value is not None else Decimal("999999")
    return priority.get(status, 99), remaining, distance, candidate["ticker"]


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
    candidates = [
        _default_candidate(
            snapshot["candidate_snapshots"][ticker],
            plans[ticker],
            research.get(ticker),
            fresh_research_checkpoint(
                preparation_id=preparation.preparation_id,
                ticker=ticker,
                preparation_created_at_utc=getattr(preparation, "created_at_utc", None),
                research_updated_at_utc=getattr(research.get(ticker), "updated_at_utc", None),
                is_deep_research=ticker in deep,
            ),
        )
        for ticker in expected
    ]
    evidence = []
    for item in snapshot["carry_forward_queue"]:
        evidence.extend(_normalize_evidence_records(item["reusable_prior_evidence"]))
    for item in research.values():
        evidence.extend(_normalize_evidence_records(item.evidence))
    payload = deepcopy(snapshot["run_template"])
    payload["candidates"] = candidates
    payload["evidence"] = evidence
    setup_counts = {
        status: sum(
            str((item.get("payload") or {}).get("setup_buyability_status") or item["buyability_status"]).upper() == status
            for item in candidates
        )
        for status in ("BUY_NOW", "ALMOST_READY", "RADAR", "NOT_ELIGIBLE")
    }
    payload["summary"] = {
        **payload["summary"],
        "preparation_id": preparation_id,
        "preparation_revision": int(snapshot.get("preparation_revision") or 0),
        "candidate_counts": {
            status: sum(item["buyability_status"] == status for item in candidates)
            for status in ("BUY_NOW", "ALMOST_READY", "RADAR", "NOT_ELIGIBLE")
        },
        "setup_counts": setup_counts,
        "market_blocked_count": sum(
            not item["market_regime_gate_passed"] for item in candidates
        ),
    }
    preparation_scope = payload["summary"].get("preparation_scope")
    if isinstance(preparation_scope, dict):
        preparation_scope["preparation_id"] = preparation_id
        created_at = getattr(preparation, "created_at_utc", None)
        if created_at is not None:
            preparation_scope["preparation_created_at_utc"] = created_at.isoformat()
    groups = presentation_groups(candidates, snapshot["report_scope"]["detailed_tickers"])
    payload["summary"]["presentation"] = {
        "watch_first_tickers": [item["ticker"] for item in groups["watch_first"]],
        "excluded_worth_reviewing_tickers": [item["ticker"] for item in groups["excluded_worth_reviewing"]],
    }
    payload["summary"]["report_focus_queue"] = [
        {"ticker": item["ticker"], "setup_score": item["setup_score"]}
        for item in groups["watch_first"]
    ]
    lines = ["# Daily Stock Alert", "", "## Watch first"]
    def render_candidate(item: dict[str, Any]) -> list[str]:
        item_payload = item.get("payload") or {}
        presentation = item_payload["presentation"]
        setup_status = str(item_payload.get("setup_buyability_status") or item["buyability_status"]).upper()
        company = (payload["summary"]["preparation_scope"].get("company_names") or {}).get(item["ticker"])
        market_blocked = not item["market_regime_gate_passed"] and setup_status != "NOT_ELIGIBLE"
        decision_text = (
            f"{setup_status} setup (MARKET BLOCKED)"
            if market_blocked
            else item["buyability_status"]
        )
        targets = item.get("structural_targets") or []
        target_text = "; ".join(
            f"${Decimal(target['price']):.2f} ({target['basis']}, {Decimal(target['r_multiple']):.2f}R)"
            for target in targets
        ) or "No structural target available from current saved levels"
        def money(value: Any) -> str:
            return f"${Decimal(str(value)):.2f}" if value is not None else "not available"
        distance = item.get("distance_to_trigger_pct")
        distance_text = f"{Decimal(str(distance)):.1f}% below trigger" if distance is not None else "trigger distance not available"
        blockers = presentation["blockers"]
        blocker_text = "\n".join(
            f"- {blocker['gate']}: {blocker['reason']} Clear when: {blocker['clear_condition']}"
            for blocker in blockers
        ) or "- None."
        return [
            f"### {item['ticker']}{' — ' + company if company else ''}",
            f"{decision_text} · Setup score **{item['setup_score']}/100**",
            f"Current {money(item.get('current_price'))} · Trigger {money(item.get('trigger_price'))} · {distance_text} · Stop {money(item.get('invalidation_price'))}",
            f"Targets: {target_text}",
            f"Decision: {presentation['classification']}",
            "Why it remains interesting: " + "; ".join(presentation["positive_factors"]),
            "Blocking gates:\n" + blocker_text,
            f"Technical trigger after eligibility: {presentation['technical_trigger']}",
            f"Invalidation level: {presentation['invalidation_level']}",
            "",
        ]
    if groups["watch_first"]:
        for item in groups["watch_first"]:
            lines.extend(render_candidate(item))
    else:
        lines.extend(["No BUY_NOW, ALMOST_READY, or RADAR candidates are in the detailed scope.", ""])
    if groups["excluded_worth_reviewing"]:
        lines.extend(["## Excluded — worth reviewing", ""])
        for item in groups["excluded_worth_reviewing"]:
            lines.extend(render_candidate(item))
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
