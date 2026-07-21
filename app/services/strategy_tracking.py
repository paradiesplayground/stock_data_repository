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
    evidence: list[dict[str, Any]] | None = None,
    strategy_name: str | None = None,
    skill_fingerprint: str | None = None,
    feature_calculation_version: str | None = None,
    data_cutoff_at_utc: str | None = None,
    notes: str | None = None,
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
    evidence = list(evidence or [])
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
        stage = _identifier(str(item.get("stage", "")), "candidate stage", 32)
        action_value = item.get("action")
        action = (
            _identifier(str(action_value), "candidate action", 32)
            if action_value
            else None
        )
        normalized_candidates.append(
            {
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
        )

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
        "candidates": normalized_candidates,
        "evidence": normalized_evidence,
        "feature_calculation_version": feature_calculation_version,
        "data_cutoff_at_utc": data_cutoff_at_utc,
        "skill_fingerprint": skill_fingerprint,
    }
    payload_hash = _canonical_hash(payload)
    existing = session.scalar(
        select(StrategyRun).where(StrategyRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise ValueError("idempotency_key already exists with a different payload")
        return {
            "run_id": existing.run_id,
            "recorded": False,
            "idempotent_replay": True,
            "payload_hash": existing.payload_hash,
        }

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
        data_cutoff_at_utc=_datetime(data_cutoff_at_utc, "data_cutoff_at_utc"),
        filters=filters,
        summary=summary,
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
    return {
        "run_id": run.run_id,
        "recorded": True,
        "idempotent_replay": False,
        "payload_hash": payload_hash,
        "candidate_count": len(normalized_candidates),
        "evidence_count": len(normalized_evidence),
    }


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
        "data_cutoff_at_utc": run.data_cutoff_at_utc.isoformat()
        if run.data_cutoff_at_utc
        else None,
        "filters": run.filters,
        "summary": run.summary,
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
        select(StrategyCandidate)
        .where(StrategyCandidate.run_id == run_id)
        .order_by(StrategyCandidate.ticker)
    ).all()
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
    return {
        "found": True,
        **_run_item(run, definition),
        "strategy_configuration": definition.configuration,
        "skill_fingerprint": definition.skill_fingerprint,
        "candidates": [
            {
                "ticker": item.ticker,
                "stage": item.stage,
                "action": item.action,
                "score": str(item.score) if item.score is not None else None,
                "score_components": item.score_components,
                "metrics": item.metrics,
                "reasons": item.reasons,
                "trade_plan": item.trade_plan,
                "payload": item.payload,
            }
            for item in candidates
        ],
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
