import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil.parser import isoparse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import (
    StrategyCandidate,
    StrategyDefinition,
    StrategyEvidence,
    StrategyOutcomeObservation,
    StrategyRun,
)
from app.strategy_decisions import (
    DECISION_CONTRACT_VERSION,
    SCREEN_BUCKETS,
    StrategyCandidateDecision,
    decision_sort_key,
    normalize_candidate_decision,
)

RUN_TYPES = {"as_run", "replay", "backtest"}
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be YYYY-MM-DD") from error


def _datetime(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = isoparse(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _identifier(value: str, field: str, maximum: int) -> str:
    normalized = value.strip().lower()
    if len(normalized) > maximum or not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field} must be {maximum} characters or fewer and contain only "
            "lowercase letters, numbers, dots, dashes, or underscores"
        )
    return normalized


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error


def _definition(
    session: Session,
    strategy_key: str,
    strategy_version: str,
    configuration: dict[str, Any],
    strategy_name: str | None,
    skill_fingerprint: str | None,
    notes: str | None,
) -> StrategyDefinition:
    definition = session.scalar(
        select(StrategyDefinition).where(
            StrategyDefinition.strategy_key == strategy_key,
            StrategyDefinition.version == strategy_version,
        )
    )
    if definition is not None:
        if _canonical_hash(definition.configuration) != _canonical_hash(configuration):
            raise ValueError(
                "strategy configuration changed; record it under a new strategy_version"
            )
        if (
            skill_fingerprint
            and definition.skill_fingerprint
            and definition.skill_fingerprint != skill_fingerprint
        ):
            raise ValueError(
                "skill fingerprint changed; record it under a new strategy_version"
            )
        return definition
    definition = StrategyDefinition(
        strategy_key=strategy_key,
        version=strategy_version,
        name=strategy_name,
        configuration=configuration,
        skill_fingerprint=skill_fingerprint,
        notes=notes,
    )
    session.add(definition)
    session.flush()
    return definition


def record_strategy_run(
    session: Session,
    *,
    strategy_key: str,
    strategy_version: str,
    as_of_date: str,
    run_type: str,
    idempotency_key: str,
    configuration: dict[str, Any],
    filters: dict[str, Any],
    candidates: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    report_markdown: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    strategy_name: str | None = None,
    skill_fingerprint: str | None = None,
    feature_calculation_version: str | None = None,
    decision_contract_version: str | None = None,
    data_cutoff_at_utc: str | None = None,
    notes: str | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    strategy_key = _identifier(strategy_key, "strategy_key", 128)
    strategy_version = _identifier(strategy_version, "strategy_version", 64)
    normalized_run_type = run_type.strip().lower()
    if normalized_run_type not in RUN_TYPES:
        raise ValueError(f"run_type must be one of: {', '.join(sorted(RUN_TYPES))}")
    if not idempotency_key.strip() or len(idempotency_key) > 255:
        raise ValueError("idempotency_key must be between 1 and 255 characters")
    if len(candidates) > 1000:
        raise ValueError("at most 1000 candidates may be recorded in one run")
    if normalized_run_type == "as_run" and decision_contract_version is None:
        raise ValueError(
            "as_run alerts require decision_contract_version="
            + DECISION_CONTRACT_VERSION
        )
    if decision_contract_version is not None:
        decision_contract_version = str(decision_contract_version).strip()
        if decision_contract_version != DECISION_CONTRACT_VERSION:
            raise ValueError(
                f"decision_contract_version must be {DECISION_CONTRACT_VERSION}"
            )
    contract_required = decision_contract_version == DECISION_CONTRACT_VERSION
    evidence = list(evidence or [])
    if report_markdown is not None:
        report_markdown = report_markdown.strip()
        if not report_markdown:
            report_markdown = None
        elif len(report_markdown) > 250_000:
            raise ValueError("report_markdown must be 250000 characters or fewer")
    if len(evidence) > 1000:
        raise ValueError("at most 1000 evidence records may be recorded in one run")

    normalized_candidates: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    for item in candidates:
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker or len(ticker) > 32:
            raise ValueError(
                "every candidate requires a ticker of 32 characters or fewer"
            )
        if ticker in seen_tickers:
            raise ValueError(f"candidate ticker appears more than once: {ticker}")
        seen_tickers.add(ticker)
        if contract_required:
            stage = _identifier(str(item.get("screen_bucket", "")), "screen_bucket", 32)
            if stage not in SCREEN_BUCKETS:
                raise ValueError(
                    "screen_bucket must be one of: " + ", ".join(sorted(SCREEN_BUCKETS))
                )
            legacy_stage = item.get("stage")
            if legacy_stage is not None and str(legacy_stage).strip().lower() != stage:
                raise ValueError(
                    "stage and screen_bucket must match when both are supplied"
                )
        else:
            stage = _identifier(str(item.get("stage", "")), "candidate stage", 32)
        action_value = item.get("action")
        if contract_required and action_value is not None:
            raise ValueError("v0.8 candidates must not emit the legacy action field")
        action = (
            _identifier(str(action_value), "candidate action", 32)
            if action_value
            else None
        )
        normalized_candidate = {
            "ticker": ticker,
            "stage": stage,
            "action": action,
            "score": str(_decimal(item.get("score"), "candidate score"))
            if item.get("score") is not None
            else None,
            "score_components": item.get("score_components"),
            "metrics": item.get("metrics"),
            "reasons": item.get("reasons"),
            "trade_plan": item.get("trade_plan"),
            "payload": item.get("payload"),
        }
        decision = normalize_candidate_decision(
            item,
            contract_version=decision_contract_version,
            contract_required=contract_required,
        )
        if decision:
            normalized_candidate["decision"] = {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in decision.items()
            }
        normalized_candidates.append(normalized_candidate)

    normalized_evidence: list[dict[str, Any]] = []
    for item in evidence:
        ticker = str(item.get("ticker", "")).strip().upper() or None
        if ticker and len(ticker) > 32:
            raise ValueError("evidence ticker must be 32 characters or fewer")
        evidence_type = _identifier(
            str(item.get("evidence_type", "")), "evidence_type", 64
        )
        normalized_evidence.append(
            {
                "ticker": ticker,
                "evidence_type": evidence_type,
                "source_url": item.get("source_url"),
                "accession_number": item.get("accession_number"),
                "published_at_utc": item.get("published_at_utc"),
                "accepted_at_utc": item.get("accepted_at_utc"),
                "retrieved_at_utc": item.get("retrieved_at_utc"),
                "summary": item.get("summary"),
                "details": item.get("details"),
            }
        )

    payload = {
        "strategy_key": strategy_key,
        "strategy_version": strategy_version,
        "as_of_date": as_of_date,
        "run_type": normalized_run_type,
        "configuration": configuration,
        "filters": filters,
        "summary": summary,
        "report_markdown": report_markdown,
        "candidates": normalized_candidates,
        "evidence": normalized_evidence,
        "feature_calculation_version": feature_calculation_version,
        "data_cutoff_at_utc": data_cutoff_at_utc,
        "skill_fingerprint": skill_fingerprint,
    }
    if decision_contract_version is not None:
        payload["decision_contract_version"] = decision_contract_version
    payload_hash = _canonical_hash(payload)
    existing = session.scalar(
        select(StrategyRun).where(StrategyRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise ValueError("idempotency_key already exists with a different payload")
        result = {
            "run_id": existing.run_id,
            "recorded": False,
            "idempotent_replay": True,
            "payload_hash": existing.payload_hash,
        }
        if existing.run_type == "as_run" and publish:
            from app.config import get_settings
            from app.services.stock_alert_delivery import publish_strategy_run_safely

            result["website_delivery"] = publish_strategy_run_safely(
                session, get_settings(), existing.run_id
            )
        return result

    definition = _definition(
        session,
        strategy_key,
        strategy_version,
        configuration,
        strategy_name,
        skill_fingerprint,
        notes,
    )
    run = StrategyRun(
        run_id=str(uuid.uuid4()),
        strategy_definition_id=definition.id,
        idempotency_key=idempotency_key,
        as_of_date=_date(as_of_date, "as_of_date"),
        run_type=normalized_run_type,
        feature_calculation_version=feature_calculation_version,
        decision_contract_version=decision_contract_version,
        data_cutoff_at_utc=_datetime(data_cutoff_at_utc, "data_cutoff_at_utc"),
        filters=filters,
        summary=summary,
        report_markdown=report_markdown,
        payload_hash=payload_hash,
    )
    session.add(run)
    session.flush()
    for item in normalized_candidates:
        session.add(
            StrategyCandidate(
                run_id=run.run_id,
                ticker=item["ticker"],
                stage=item["stage"],
                action=item["action"],
                score=_decimal(item["score"], "candidate score"),
                score_components=item["score_components"],
                metrics=item["metrics"],
                reasons=item["reasons"],
                trade_plan=item["trade_plan"],
                payload=item["payload"],
            )
        )
        decision = item.get("decision")
        if decision:
            session.add(
                StrategyCandidateDecision(
                    run_id=run.run_id,
                    ticker=item["ticker"],
                    decision_status=decision.get("decision_status") or decision.get("buyability_status"),
                    status_reason=decision["status_reason"],
                    next_condition=decision.get("next_condition") or "; ".join(decision.get("buy_conditions", [])),
                    technical_state=decision["technical_state"],
                    current_entry=_decimal(decision.get("current_entry"), "current_entry"),
                    pct_above_trigger=_decimal(
                        decision.get("pct_above_trigger"), "pct_above_trigger"
                    ),
                    t1_r=_decimal(decision.get("t1_r"), "t1_r"),
                    t2_r=_decimal(decision.get("t2_r"), "t2_r"),
                    technical_gate_passed=decision["technical_gate_passed"],
                    market_regime_gate_passed=decision["market_regime_gate_passed"],
                    buyability_status=decision.get("buyability_status"),
                    buy_conditions=decision.get("buy_conditions"),
                    remaining_gate_count=decision.get("remaining_gate_count"),
                    current_price=_decimal(decision.get("current_price"), "current_price"),
                    trigger_price=_decimal(decision.get("trigger_price"), "trigger_price"),
                    distance_to_trigger_pct=_decimal(decision.get("distance_to_trigger_pct"), "distance_to_trigger_pct"),
                    invalidation_price=_decimal(decision.get("invalidation_price"), "invalidation_price"),
                )
            )
    for item in normalized_evidence:
        session.add(
            StrategyEvidence(
                run_id=run.run_id,
                ticker=item["ticker"],
                evidence_type=item["evidence_type"],
                source_url=item["source_url"],
                accession_number=item["accession_number"],
                published_at_utc=_datetime(
                    item["published_at_utc"], "published_at_utc"
                ),
                accepted_at_utc=_datetime(item["accepted_at_utc"], "accepted_at_utc"),
                retrieved_at_utc=_datetime(
                    item["retrieved_at_utc"], "retrieved_at_utc"
                ),
                summary=item["summary"],
                details=item["details"],
            )
        )
    session.commit()
    result = {
        "run_id": run.run_id,
        "recorded": True,
        "idempotent_replay": False,
        "payload_hash": payload_hash,
        "candidate_count": len(normalized_candidates),
        "evidence_count": len(normalized_evidence),
    }
    if normalized_run_type == "as_run" and publish:
        from app.config import get_settings
        from app.services.stock_alert_delivery import publish_strategy_run_safely

        result["website_delivery"] = publish_strategy_run_safely(
            session, get_settings(), run.run_id
        )
    return result


def record_strategy_outcomes(
    session: Session,
    run_id: str,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    run = session.get(StrategyRun, run_id)
    if run is None:
        raise ValueError("run_id was not found")
    if len(observations) > 1000:
        raise ValueError("at most 1000 observations may be recorded at once")
    allowed_tickers = set(
        session.scalars(
            select(StrategyCandidate.ticker).where(StrategyCandidate.run_id == run_id)
        ).all()
    )
    recorded = duplicates = 0
    for item in observations:
        ticker = str(item.get("ticker", "")).strip().upper()
        if ticker not in allowed_tickers:
            raise ValueError(f"ticker is not a candidate in this run: {ticker}")
        observation_date = _date(
            str(item.get("observation_date", "")), "observation_date"
        )
        horizon = _identifier(str(item.get("horizon", "")), "horizon", 32)
        existing = session.scalar(
            select(StrategyOutcomeObservation).where(
                StrategyOutcomeObservation.run_id == run_id,
                StrategyOutcomeObservation.ticker == ticker,
                StrategyOutcomeObservation.observation_date == observation_date,
                StrategyOutcomeObservation.horizon == horizon,
            )
        )
        incoming_hash = _canonical_hash(
            {
                "status": item.get("status"),
                "metrics": item.get("metrics") or {},
                "execution_assumptions": item.get("execution_assumptions"),
            }
        )
        if existing is not None:
            existing_hash = _canonical_hash(
                {
                    "status": existing.status,
                    "metrics": existing.metrics,
                    "execution_assumptions": existing.execution_assumptions,
                }
            )
            if existing_hash != incoming_hash:
                raise ValueError(
                    "outcome observation already exists with different values"
                )
            duplicates += 1
            continue
        session.add(
            StrategyOutcomeObservation(
                run_id=run_id,
                ticker=ticker,
                observation_date=observation_date,
                horizon=horizon,
                status=item.get("status"),
                metrics=item.get("metrics") or {},
                execution_assumptions=item.get("execution_assumptions"),
            )
        )
        recorded += 1
    session.commit()
    return {"run_id": run_id, "recorded": recorded, "duplicates": duplicates}


def _run_item(run: StrategyRun, definition: StrategyDefinition) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "strategy_key": definition.strategy_key,
        "strategy_version": definition.version,
        "strategy_name": definition.name,
        "as_of_date": run.as_of_date.isoformat(),
        "run_type": run.run_type,
        "feature_calculation_version": run.feature_calculation_version,
        "decision_contract_version": getattr(run, "decision_contract_version", None),
        "data_cutoff_at_utc": run.data_cutoff_at_utc.isoformat()
        if run.data_cutoff_at_utc
        else None,
        "filters": run.filters,
        "summary": run.summary,
        "report_markdown": run.report_markdown,
        "payload_hash": run.payload_hash,
        "generated_at_utc": run.generated_at_utc.isoformat(),
    }


def list_strategy_runs(
    session: Session,
    strategy_key: str | None = None,
    strategy_version: str | None = None,
    run_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    statement = select(StrategyRun, StrategyDefinition).join(
        StrategyDefinition, StrategyDefinition.id == StrategyRun.strategy_definition_id
    )
    if strategy_key:
        statement = statement.where(
            StrategyDefinition.strategy_key == strategy_key.strip().lower()
        )
    if strategy_version:
        statement = statement.where(
            StrategyDefinition.version == strategy_version.strip().lower()
        )
    if run_type:
        statement = statement.where(StrategyRun.run_type == run_type.strip().lower())
    if start_date:
        statement = statement.where(
            StrategyRun.as_of_date >= _date(start_date, "start_date")
        )
    if end_date:
        statement = statement.where(
            StrategyRun.as_of_date <= _date(end_date, "end_date")
        )
    rows = session.execute(
        statement.order_by(
            desc(StrategyRun.as_of_date), desc(StrategyRun.generated_at_utc)
        ).limit(limit)
    ).all()
    return {
        "count": len(rows),
        "items": [_run_item(run, definition) for run, definition in rows],
    }


def get_strategy_run(session: Session, run_id: str) -> dict[str, Any]:
    row = session.execute(
        select(StrategyRun, StrategyDefinition)
        .join(
            StrategyDefinition,
            StrategyDefinition.id == StrategyRun.strategy_definition_id,
        )
        .where(StrategyRun.run_id == run_id)
    ).one_or_none()
    if row is None:
        return {"run_id": run_id, "found": False}
    run, definition = row
    candidates = session.scalars(
        select(StrategyCandidate).where(StrategyCandidate.run_id == run_id)
    ).all()
    decisions = session.scalars(
        select(StrategyCandidateDecision).where(
            StrategyCandidateDecision.run_id == run_id
        )
    ).all()
    decisions_by_ticker = {item.ticker: item for item in decisions}
    candidates = sorted(
        candidates,
        key=lambda item: decision_sort_key(
            decisions_by_ticker.get(item.ticker).decision_status
            if item.ticker in decisions_by_ticker
            else None,
            item.score,
            item.ticker,
            getattr(run, "decision_contract_version", None),
        ),
    )
    evidence = session.scalars(
        select(StrategyEvidence)
        .where(StrategyEvidence.run_id == run_id)
        .order_by(StrategyEvidence.id)
    ).all()
    outcomes = session.scalars(
        select(StrategyOutcomeObservation)
        .where(StrategyOutcomeObservation.run_id == run_id)
        .order_by(
            StrategyOutcomeObservation.observation_date,
            StrategyOutcomeObservation.ticker,
        )
    ).all()

    candidate_items = []
    for item in candidates:
        decision = decisions_by_ticker.get(item.ticker)
        candidate_item = {
                "ticker": item.ticker,
                "stage": item.stage,
                "screen_bucket": item.stage,
                "action": item.action,
                "score": str(item.score) if item.score is not None else None,
                "score_components": item.score_components,
                "metrics": item.metrics,
                "reasons": item.reasons,
                "trade_plan": item.trade_plan,
                "payload": item.payload,
                "decision_status": decision.decision_status if decision else None,
                "status_reason": decision.status_reason if decision else None,
                "next_condition": decision.next_condition if decision else None,
                "technical_state": getattr(decision, "technical_state", None)
                if decision
                else None,
                "current_entry": str(decision.current_entry)
                if decision and decision.current_entry is not None
                else None,
                "pct_above_trigger": str(decision.pct_above_trigger)
                if decision and decision.pct_above_trigger is not None
                else None,
                "t1_r": str(decision.t1_r)
                if decision and decision.t1_r is not None
                else None,
                "t2_r": str(decision.t2_r)
                if decision and decision.t2_r is not None
                else None,
                "technical_gate_passed": decision.technical_gate_passed
                if decision
                else None,
                "market_regime_gate_passed": decision.market_regime_gate_passed
                if decision
                else None,
            }
        if getattr(run, "decision_contract_version", None) == DECISION_CONTRACT_VERSION:
            candidate_item.pop("action", None)
            candidate_item.pop("decision_status", None)
            candidate_item.pop("next_condition", None)
            candidate_item.pop("current_entry", None)
            candidate_item.pop("pct_above_trigger", None)
            candidate_item.pop("t1_r", None)
            candidate_item.pop("t2_r", None)
            candidate_item.update(
                {
                    "buyability_status": decision.buyability_status if decision else None,
                    "buy_conditions": decision.buy_conditions if decision else None,
                    "remaining_gate_count": decision.remaining_gate_count if decision else None,
                    "current_price": str(decision.current_price) if decision and decision.current_price is not None else None,
                    "trigger_price": str(decision.trigger_price) if decision and decision.trigger_price is not None else None,
                    "distance_to_trigger_pct": str(decision.distance_to_trigger_pct) if decision and decision.distance_to_trigger_pct is not None else None,
                    "invalidation_price": str(decision.invalidation_price) if decision and decision.invalidation_price is not None else None,
                }
            )
        candidate_items.append(candidate_item)

    return {
        "found": True,
        **_run_item(run, definition),
        "strategy_configuration": definition.configuration,
        "skill_fingerprint": definition.skill_fingerprint,
        "candidates": candidate_items,
        "evidence": [
            {
                "ticker": item.ticker,
                "evidence_type": item.evidence_type,
                "source_url": item.source_url,
                "accession_number": item.accession_number,
                "published_at_utc": item.published_at_utc.isoformat()
                if item.published_at_utc
                else None,
                "accepted_at_utc": item.accepted_at_utc.isoformat()
                if item.accepted_at_utc
                else None,
                "retrieved_at_utc": item.retrieved_at_utc.isoformat()
                if item.retrieved_at_utc
                else None,
                "summary": item.summary,
                "details": item.details,
            }
            for item in evidence
        ],
        "outcomes": [
            {
                "ticker": item.ticker,
                "observation_date": item.observation_date.isoformat(),
                "horizon": item.horizon,
                "status": item.status,
                "metrics": item.metrics,
                "execution_assumptions": item.execution_assumptions,
                "observed_at_utc": item.observed_at_utc.isoformat(),
            }
            for item in outcomes
        ],
    }
