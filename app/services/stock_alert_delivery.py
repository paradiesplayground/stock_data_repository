import logging
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import StrategyDefinition, StrategyRun
from app.services.strategy_tracking import get_strategy_run

logger = logging.getLogger(__name__)


def publish_strategy_run(
    session: Session,
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    if not settings.stock_alert_webhook_url or not settings.stock_alert_webhook_token:
        return {"status": "disabled", "run_id": run_id}

    run = get_strategy_run(session, run_id)
    if not run.get("found"):
        raise ValueError("run_id was not found")
    if run["run_type"] != "as_run":
        return {"status": "skipped", "reason": "not_as_run", "run_id": run_id}

    response = httpx.post(
        settings.stock_alert_webhook_url,
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
    if result.get("email") == "failed":
        raise RuntimeError("website published the alert but email delivery failed")
    return {"status": result.get("status", "published"), "run_id": run_id}


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
