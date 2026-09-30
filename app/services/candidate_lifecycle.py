from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DailyPriceBar, StrategyCandidateLifecycle, StrategyCandidateLifecycleObservation

LIFECYCLE_STATES = {"FALLEN", "RECOVERING", "NEAR_TRIGGER", "ACTIONABLE", "INVALIDATED"}
STALE_TIMEOUT_TRADING_SESSIONS = 30
STALE_TIMEOUT_REASON = "candidate absent from discovery for {sessions} trading sessions"


def _decimal(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def transition_state(candidate: dict[str, Any], previous_state: str | None) -> str:
    if previous_state == "INVALIDATED":
        return "INVALIDATED"
    status = str(candidate.get("buyability_status") or "").upper()
    if status in {"BUY_NOW", "ALMOST_READY"}:
        return "ACTIONABLE"
    if candidate.get("deterministic_risk_flags"):
        return "INVALIDATED"
    distance = _decimal(candidate.get("distance_to_trigger_pct"))
    if distance is not None and Decimal("0") <= distance <= Decimal("10"):
        return "NEAR_TRIGGER"
    if previous_state in {"FALLEN", "RECOVERING", "NEAR_TRIGGER"}:
        decline = _decimal((candidate.get("metrics") or {}).get("price_change_12w_pct"))
        if decline is not None and decline > Decimal("-20"):
            return "RECOVERING"
        return previous_state
    return "FALLEN"


def anchored_trigger(candidate: dict[str, Any], lifecycle: StrategyCandidateLifecycle | None) -> Decimal | None:
    if lifecycle and lifecycle.active_trigger is not None and lifecycle.lifecycle_state != "INVALIDATED":
        return _decimal(lifecycle.active_trigger)
    return _decimal(candidate.get("trigger_price") or candidate.get("suggested_trigger_price"))


def apply_active_trigger(candidate: dict[str, Any], lifecycle: StrategyCandidateLifecycle | None) -> dict[str, Any]:
    trigger = anchored_trigger(candidate, lifecycle)
    metrics = candidate.get("metrics") or candidate.get("deterministic_metrics") or {}
    price = _decimal(candidate.get("current_price") or metrics.get("close"))
    if trigger is None or price is None:
        return candidate
    candidate["trigger_price"] = str(trigger)
    candidate["distance_to_trigger_pct"] = str(((trigger - price) / trigger) * Decimal("100"))
    candidate.setdefault("payload", {})["rolling_trigger"] = candidate.get("suggested_trigger_price") or candidate.get("trigger_price")
    candidate["payload"]["active_trigger"] = str(trigger)
    candidate["payload"]["active_trigger_set_date"] = lifecycle.active_trigger_set_date.isoformat() if lifecycle and lifecycle.active_trigger_set_date else None
    return candidate


def get_lifecycle(session: Session, strategy_key: str, ticker: str) -> StrategyCandidateLifecycle | None:
    if not hasattr(session, "scalar"):
        return None
    return session.scalar(select(StrategyCandidateLifecycle).where(
        StrategyCandidateLifecycle.strategy_key == strategy_key,
        StrategyCandidateLifecycle.ticker == ticker,
    ))


def trading_sessions_since(session: Session, *, start_date: date, end_date: date) -> int:
    """Count completed market sessions in the stored price universe."""
    if end_date <= start_date or not hasattr(session, "scalar"):
        return 0
    return int(session.scalar(select(func.count(func.distinct(DailyPriceBar.trade_date))).where(
        DailyPriceBar.trade_date > start_date,
        DailyPriceBar.trade_date <= end_date,
    )) or 0)


def archive_stale_lifecycles(
    session: Session,
    *,
    strategy_key: str,
    as_of_date: date,
    observed_tickers: set[str],
    timeout_trading_sessions: int = STALE_TIMEOUT_TRADING_SESSIONS,
    source_run_id: str | None = None,
) -> list[str]:
    """Archive candidates absent from the current discovery universe after the configured timeout."""
    if timeout_trading_sessions < 1 or not hasattr(session, "scalars"):
        return []
    rows = session.scalars(select(StrategyCandidateLifecycle).where(
        StrategyCandidateLifecycle.strategy_key == strategy_key,
        StrategyCandidateLifecycle.lifecycle_state != "INVALIDATED",
    )).all()
    archived: list[str] = []
    for row in rows:
        if row.ticker.upper() in observed_tickers:
            continue
        stale_anchor = row.last_discovery_screen_date or row.first_discovered_date
        if stale_anchor is None:
            continue
        sessions_absent = trading_sessions_since(
            session,
            start_date=stale_anchor,
            end_date=as_of_date,
        )
        if sessions_absent < timeout_trading_sessions:
            continue
        previous_state = row.lifecycle_state
        row.lifecycle_state = "INVALIDATED"
        row.lifecycle_state_since = as_of_date
        row.last_material_event = "STALE_TIMEOUT"
        row.last_material_event_date = as_of_date
        row.archive_reason = STALE_TIMEOUT_REASON.format(sessions=sessions_absent)
        row.archived_date = as_of_date
        row.active_trigger = None
        if hasattr(session, "add"):
            session.add(StrategyCandidateLifecycleObservation(
                strategy_key=strategy_key,
                ticker=row.ticker,
                observation_date=as_of_date,
                from_state=previous_state,
                to_state="INVALIDATED",
                event="STALE_TIMEOUT",
                outcome_status="INVALIDATED",
                metrics={"sessions_absent": sessions_absent, "archive_reason": row.archive_reason},
                source_run_id=source_run_id,
            ))
        archived.append(row.ticker)
    return archived


def persist_lifecycle(
    session: Session,
    *,
    strategy_key: str,
    as_of_date: date,
    candidate: dict[str, Any],
    previous: StrategyCandidateLifecycle | None,
    source_run_id: str | None = None,
) -> StrategyCandidateLifecycle:
    ticker = str(candidate["ticker"]).upper()
    state = transition_state(candidate, previous.lifecycle_state if previous else None)
    row = previous or StrategyCandidateLifecycle(
        strategy_key=strategy_key, ticker=ticker, first_discovered_date=as_of_date,
        lifecycle_state=state, lifecycle_state_since=as_of_date,
    )
    if previous and state != previous.lifecycle_state:
        row.lifecycle_state_since = as_of_date
        row.last_material_event = f"{previous.lifecycle_state}_TO_{state}"
        row.last_material_event_date = as_of_date
        if hasattr(session, "add"):
            session.add(StrategyCandidateLifecycleObservation(
                strategy_key=strategy_key,
                ticker=ticker,
                observation_date=as_of_date,
                from_state=previous.lifecycle_state,
                to_state=state,
                event=row.last_material_event,
                outcome_status=state,
                metrics={
                    "current_price": candidate.get("current_price"),
                    "active_trigger": str(row.active_trigger) if row.active_trigger is not None else None,
                    "rolling_trigger": candidate.get("trigger_price"),
                    "distance_to_trigger_pct": candidate.get("distance_to_trigger_pct"),
                },
                source_run_id=source_run_id,
            ))
    row.lifecycle_state = state
    row.last_discovery_screen_date = as_of_date if (candidate.get("payload") or {}).get("in_raw_pool", True) else row.last_discovery_screen_date
    row.days_on_watch = max(0, (as_of_date - row.first_discovered_date).days)
    metrics = candidate.get("metrics") or {}
    row.discovery_price = row.discovery_price or _decimal(candidate.get("current_price") or metrics.get("close"))
    row.discovery_decline_metric = row.discovery_decline_metric or _decimal(metrics.get("price_change_12w_pct"))
    row.rolling_trigger = _decimal((candidate.get("payload") or {}).get("rolling_trigger") or candidate.get("trigger_price"))
    row.current_trigger_distance_pct = _decimal(candidate.get("distance_to_trigger_pct"))
    if row.current_trigger_distance_pct is not None and (row.best_trigger_distance_pct is None or row.current_trigger_distance_pct < row.best_trigger_distance_pct):
        row.best_trigger_distance_pct = row.current_trigger_distance_pct
    row.previous_relative_strength_20d = row.relative_strength_20d
    row.relative_strength_20d = _decimal(metrics.get("relative_return_20d_vs_qqq_pct"))
    if state in {"INVALIDATED"}:
        row.archive_reason = row.archive_reason or "deterministic risk or structural invalidation"
        row.archived_date = row.archived_date or as_of_date
        row.active_trigger = None
    elif row.active_trigger is None and row.current_trigger_distance_pct is not None and Decimal("0") <= row.current_trigger_distance_pct <= Decimal("10"):
        row.active_trigger = row.rolling_trigger
        row.active_trigger_set_date = as_of_date
    session.add(row)
    return row
