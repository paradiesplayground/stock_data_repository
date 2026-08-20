import logging
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import StrategyDefinition, StrategyRun
from app.services.strategy_tracking import get_strategy_run
from app.strategy_decisions import (
    DECISION_CONTRACT_VERSION,
    normalize_candidate_decision,
)

logger = logging.getLogger(__name__)


def validate_strategy_run_for_delivery(run: dict[str, Any]) -> None:
    if run.get("decision_contract_version") != DECISION_CONTRACT_VERSION:
        raise ValueError(
            "production alerts require decision_contract_version="
            + DECISION_CONTRACT_VERSION
        )
    for candidate in run.get("candidates") or []:
        ticker = candidate.get("ticker") or "<unknown>"
        try:
            normalize_candidate_decision(candidate, contract_required=True)
        except ValueError as error:
            raise ValueError(
                f"decision contract validation failed before delivery for {ticker}: {error}"
            ) from error


def _deliver_strategy_run(
    session: Session,
    settings: Settings,
    run_id: str,
    *,
    resend_email: bool,
) -> dict[str, Any]:
    if not settings.stock_alert_webhook_url or not settings.stock_alert_webhook_token:
        return {"status": "disabled", "run_id": run_id}

    run = get_strategy_run(session, run_id)
    if not run.get("found"):
        raise ValueError("run_id was not found")
    if run["run_type"] != "as_run":
        return {"status": "skipped", "reason": "not_as_run", "run_id": run_id}
    validate_strategy_run_for_delivery(run)

    payload = dict(run)
    if resend_email:
        payload["delivery_request"] = "resend_email"

    response = httpx.post(
        settings.stock_alert_webhook_url,
        headers={
            "Authorization": f"Bearer {settings.stock_alert_webhook_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.stock_alert_webhook_timeout_seconds,
        follow_redirects=True,
    )
    response.raise_for_status()
    result = response.json()
    website_delivery = result.get("status")
    email_delivery = result.get("email")
    if website_delivery != "published":
        raise RuntimeError(
            "website did not confirm publication: " + str(website_delivery or "missing")
        )
    if email_delivery != "sent":
        raise RuntimeError(
            "website did not confirm email delivery: " + str(email_delivery or "missing")
        )
    receipt = result.get("email_receipt")
    if not isinstance(receipt, dict):
        raise RuntimeError("website did not return an email delivery receipt")
    if not receipt.get("message_id"):
        raise RuntimeError("email delivery receipt did not include message_id")
    if int(receipt.get("accepted_count") or 0) < 1:
        raise RuntimeError("email delivery receipt did not confirm an accepted recipient")

    return {
        "status": "resent" if resend_email else "published",
        "run_id": run_id,
        "website_delivery": result.get("publication") or "published",
        "email_delivery": "smtp_accepted",
        "email_receipt": receipt,
    }


def publish_strategy_run(
    session: Session,
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    return _deliver_strategy_run(
        session,
        settings,
        run_id,
        resend_email=False,
    )


def resend_strategy_run_email(
    session: Session,
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    """Explicitly resend email for a stored canonical run without creating another run."""
    return _deliver_strategy_run(
        session,
        settings,
        run_id,
        resend_email=True,
    )


def publish_latest_strategy_run(
    session: Session,
    settings: Settings,
) -> dict[str, Any]:
    run_id = session.scalar(
        select(StrategyRun.run_id)
        .join(
            StrategyDefinition,
            StrategyDefinition.id == StrategyRun.strategy_definition_id,
        )
        .where(StrategyRun.run_type == "as_run")
        .order_by(desc(StrategyRun.as_of_date), desc(StrategyRun.generated_at_utc))
        .limit(1)
    )
    if run_id is None:
        raise ValueError("no as_run strategy run was found")
    return publish_strategy_run(session, settings, run_id)


def publish_strategy_run_safely(
    session: Session,
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    try:
        return publish_strategy_run(session, settings, run_id)
    except Exception as error:
        logger.exception("Stock alert delivery failed for run %s", run_id)
        return {"status": "failed", "run_id": run_id, "error": str(error)}
