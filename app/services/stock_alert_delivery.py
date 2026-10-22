import logging
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import StrategyDefinition, StrategyRun
from app.decision_contract import SUPPORTED_DECISION_CONTRACT_VERSIONS
from app.services.strategy_tracking import get_strategy_run
from app.strategy_decisions import normalize_candidate_decision

logger = logging.getLogger(__name__)


def validate_strategy_run_for_delivery(run: dict[str, Any]) -> None:
    contract_version = str(run.get("decision_contract_version") or "")
    if contract_version not in SUPPORTED_DECISION_CONTRACT_VERSIONS:
        raise ValueError(
            "production alerts require a supported decision_contract_version"
        )
    for candidate in run.get("candidates") or []:
        ticker = candidate.get("ticker") or "<unknown>"
        try:
            normalize_candidate_decision(
                candidate,
                contract_version=contract_version,
                contract_required=True,
            )
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


def _request_strategy_run_delivery(
    session: Session,
    settings: Settings,
    run_id: str,
    *,
    delivery_request: str,
    rendering_revision: dict[str, Any] | None = None,
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
    payload["delivery_request"] = delivery_request
    # Stable across worker restarts. The receiving website must treat this as an
    # idempotency key for the individual destination, not the whole alert.
    payload["delivery_id"] = f"{run_id}:{delivery_request}"
    if rendering_revision is not None:
        payload["rendering_revision"] = rendering_revision

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
    if result.get("status") != "published":
        raise RuntimeError(
            "website did not confirm publication: "
            + str(result.get("status") or "missing")
        )
    return result


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


def publish_strategy_run_only(session: Session, settings: Settings, run_id: str) -> dict[str, Any]:
    """Publish the immutable run without requesting email delivery."""
    try:
        result = _request_strategy_run_delivery(
            session, settings, run_id, delivery_request="publish_only"
        )
    except RuntimeError as error:
        # A previous successful request may have reached the website before a
        # worker crash persisted its receipt. The immutable run already exists,
        # so a duplicate is positive publication confirmation, not a retry.
        if "duplicate" not in str(error).lower():
            raise
        result = {"publication": "existing"}
    return {
        "status": "published", "run_id": run_id,
        "website_delivery": result.get("publication") or "published",
    }


def send_strategy_run_email(session: Session, settings: Settings, run_id: str) -> dict[str, Any]:
    """Request email only after the website has durably published the run."""
    result = _request_strategy_run_delivery(
        session, settings, run_id, delivery_request="send_email"
    )
    if result.get("email") != "sent":
        raise RuntimeError("website did not confirm email delivery: " + str(result.get("email") or "missing"))
    receipt = result.get("email_receipt")
    if not isinstance(receipt, dict) or not receipt.get("message_id") or int(receipt.get("accepted_count") or 0) < 1:
        raise RuntimeError("email delivery receipt did not confirm an accepted recipient")
    return {"status": "sent", "run_id": run_id, "email_delivery": "smtp_accepted", "email_receipt": receipt}


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


def publish_strategy_run_revision(
    session: Session,
    settings: Settings,
    run_id: str,
    renderer_version: str,
    reason: str,
    *,
    resend_email: bool = False,
) -> dict[str, Any]:
    """Append a rendering revision without changing the immutable source run."""
    renderer_version = renderer_version.strip()
    reason = reason.strip()
    if not renderer_version:
        raise ValueError("renderer_version is required")
    if not reason:
        raise ValueError("reason is required")
    result = _request_strategy_run_delivery(
        session,
        settings,
        run_id,
        delivery_request="publish_revision",
        rendering_revision={
            "renderer_version": renderer_version,
            "reason": reason,
            "resend_email": resend_email,
        },
    )
    revision = result.get("rendering_revision")
    if result.get("publication") != "revised" or not isinstance(revision, dict):
        raise RuntimeError("website did not confirm the rendering revision")
    return {
        "status": "revised",
        "run_id": run_id,
        "rendering_revision": revision,
        "email": result.get("email", "not_requested"),
        "email_receipt": result.get("email_receipt"),
    }


def verify_strategy_run_email(
    session: Session,
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    """Ask the website to verify the accepted message in the configured mailbox."""
    result = _request_strategy_run_delivery(
        session,
        settings,
        run_id,
        delivery_request="verify_email",
    )
    verification = result.get("mailbox_verification")
    if not isinstance(verification, dict) or not verification.get("status"):
        raise RuntimeError("website did not return mailbox verification status")
    return {
        "status": verification["status"],
        "run_id": run_id,
        "mailbox_verification": verification,
    }


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


def _website_endpoint(settings: Settings, suffix: str) -> str:
    """Sibling endpoint of the stock-alert webhook (for example ``.../stock-alerts/shadow``)."""
    return settings.stock_alert_webhook_url.rstrip("/") + "/" + suffix


def publish_shadow_run(session: Session, settings: Settings, run_id: str) -> dict[str, Any]:
    """Store a v0.9 shadow run on the website's unlisted shadow page. Never emails."""
    if not settings.stock_alert_webhook_url or not settings.stock_alert_webhook_token:
        return {"status": "disabled", "run_id": run_id}
    run = get_strategy_run(session, run_id)
    if not run.get("found"):
        raise ValueError("run_id was not found")
    if run["run_type"] != "shadow":
        raise ValueError("only shadow runs are published to the shadow page")
    run = with_shadow_presentation(run)
    response = httpx.post(
        _website_endpoint(settings, "shadow"),
        headers={
            "Authorization": f"Bearer {settings.stock_alert_webhook_token}",
            "Content-Type": "application/json",
        },
        json=run,
        timeout=settings.stock_alert_webhook_timeout_seconds,
        follow_redirects=True,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("status") != "stored":
        raise RuntimeError("website did not confirm the shadow run: " + str(result.get("status") or "missing"))
    return {"status": "published", "run_id": run_id, "publication": result.get("publication")}


def send_weekly_comparison(settings: Settings, comparison: dict[str, Any]) -> dict[str, Any]:
    """Store the weekly v0.8-vs-v0.9 comparison on the website and email it once per week."""
    if not settings.stock_alert_webhook_url or not settings.stock_alert_webhook_token:
        return {"status": "disabled", "week_key": comparison.get("week_key")}
    response = httpx.post(
        _website_endpoint(settings, "weekly-comparison"),
        headers={
            "Authorization": f"Bearer {settings.stock_alert_webhook_token}",
            "Content-Type": "application/json",
        },
        json=comparison,
        timeout=settings.stock_alert_webhook_timeout_seconds,
        follow_redirects=True,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("email") != "sent":
        raise RuntimeError("website did not confirm the weekly comparison email: " + str(result.get("email") or "missing"))
    return {"status": "sent", "week_key": comparison.get("week_key"), "email_receipt": result.get("email_receipt")}


def with_shadow_presentation(run: dict[str, Any]) -> dict[str, Any]:
    """Shadow runs recorded before the presentation existed get it derived at publication.

    The stored run is never rewritten; the presentation is rebuilt from the run's
    own stored candidates and capped sections, which is what it would have held.
    """
    summary = dict(run.get("summary") or {})
    if "presentation_v09" in summary or "email_sections" not in summary:
        return run
    from datetime import date

    from app.services.alert_v09_presentation import build_presentation, render_markdown

    presentation = build_presentation(
        date.fromisoformat(str(run["as_of_date"])),
        list(run.get("candidates") or []),
        summary["email_sections"],
        market_ok=bool((summary.get("market_regime") or {}).get("gate_passed")),
        shadow=True,
    )
    summary["presentation_v09"] = presentation
    return {
        **run,
        "summary": summary,
        "report_markdown": render_markdown(presentation),
        "presentation_derived_at_publication": True,
    }
