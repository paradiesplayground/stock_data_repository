from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.mcp_queries import get_data_freshness
from app.services.stock_alert_delivery import (
    publish_strategy_run,
    verify_strategy_run_email,
)
from app.services.strategy_tracking import get_strategy_run, record_strategy_run

PRODUCTION_STRATEGY_KEY = "dynamic_swing_buy_alerts"
PRODUCTION_STRATEGY_VERSION = "0.7"


def _validate_prepared_scope(payload: dict[str, Any]) -> None:
    if (
        payload.get("strategy_key") != PRODUCTION_STRATEGY_KEY
        or payload.get("strategy_version") != PRODUCTION_STRATEGY_VERSION
    ):
        return

    report = payload.get("report_markdown")
    if not isinstance(report, str) or not report.strip():
        raise ValueError("prepared production alert requires completed report_markdown")

    summary = payload.get("summary")
    scope = summary.get("preparation_scope") if isinstance(summary, dict) else None
    expected = scope.get("expected_candidate_tickers") if isinstance(scope, dict) else None
    if not isinstance(expected, list) or any(not isinstance(item, str) for item in expected):
        raise ValueError(
            "prepared production alert requires summary.preparation_scope."
            "expected_candidate_tickers"
        )

    expected_tickers = {item.strip().upper() for item in expected if item.strip()}
    submitted: list[str] = []
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict) or not str(candidate.get("ticker") or "").strip():
            raise ValueError("every prepared candidate requires a ticker")
        submitted.append(str(candidate["ticker"]).strip().upper())

    duplicates = sorted({ticker for ticker in submitted if submitted.count(ticker) > 1})
    submitted_tickers = set(submitted)
    missing = sorted(expected_tickers - submitted_tickers)
    unexpected = sorted(submitted_tickers - expected_tickers)
    problems = []
    if missing:
        problems.append("missing prepared candidates: " + ", ".join(missing))
    if unexpected:
        problems.append("unexpected candidates: " + ", ".join(unexpected))
    if duplicates:
        problems.append("duplicate candidates: " + ", ".join(duplicates))
    if problems:
        raise ValueError("; ".join(problems))


def run_daily_stock_alert(
    session: Session,
    settings: Settings,
    *,
    as_of_date: str,
    run_payload: dict[str, Any],
    verify_mailbox: bool = False,
) -> dict[str, Any]:
    """Complete a prepared production alert through one resumable server-side call."""
    payload = dict(run_payload)
    if payload.get("run_type") != "as_run":
        raise ValueError("run_payload.run_type must be as_run")
    if payload.get("as_of_date") != as_of_date:
        raise ValueError("run_payload.as_of_date must match as_of_date")
    if "publish" in payload:
        raise ValueError("run_payload must not contain publish")
    _validate_prepared_scope(payload)

    freshness = get_data_freshness(session, settings)
    if freshness.get("expected_market_date") != as_of_date:
        raise ValueError(
            "as_of_date must match the current expected market date "
            + str(freshness.get("expected_market_date") or "<unknown>")
        )
    if not freshness.get("ready_for_screening"):
        issues = freshness.get("freshness_issues") or []
        detail = "; ".join(str(item) for item in issues) or "market or features are stale"
        raise RuntimeError("data is not ready for screening: " + detail)

    recorded = record_strategy_run(session, **payload, publish=False)
    run_id = str(recorded["run_id"])
    persisted = get_strategy_run(session, run_id)
    if not persisted.get("found"):
        raise RuntimeError("canonical run could not be read back after persistence")
    if persisted.get("as_of_date") != as_of_date:
        raise RuntimeError("persisted run date does not match requested alert date")
    if persisted.get("payload_hash") != recorded.get("payload_hash"):
        raise RuntimeError("persisted run hash does not match the recorded payload hash")

    delivery = publish_strategy_run(session, settings, run_id)
    result: dict[str, Any] = {
        "status": "completed",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "recorded": recorded.get("recorded", False),
        "idempotent_replay": recorded.get("idempotent_replay", False),
        "payload_hash": recorded.get("payload_hash"),
        "freshness": {
            "checked_at_local": freshness.get("checked_at_local"),
            "latest_trade_date": freshness.get("latest_trade_date"),
            "latest_feature_date": freshness.get("latest_feature_date"),
        },
        "publication": delivery,
        "mailbox_verification": {"status": "not_requested"},
    }
    if verify_mailbox:
        verification = verify_strategy_run_email(session, settings, run_id)
        result["mailbox_verification"] = verification["mailbox_verification"]
        if verification.get("status") != "verified":
            result["status"] = "completed_with_unverified_mailbox"
    return result
