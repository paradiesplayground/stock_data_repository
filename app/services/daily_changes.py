from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.services.strategy_tracking import get_strategy_run, list_strategy_runs


STATUS_PRIORITY = {
    "BUY_NOW": 0,
    "ALMOST_READY": 1,
    "RADAR": 2,
    "NOT_ELIGIBLE": 3,
}
LEGACY_STATUS_EQUIVALENTS = {
    "BUY_SETUP": "BUY_NOW",
    "CONFIRMED_WAIT_FOR_ENTRY": "ALMOST_READY",
    "NEAR_TRIGGER": "ALMOST_READY",
    "WATCH": "RADAR",
    "RESEARCH": "NOT_ELIGIBLE",
    "AVOID": "NOT_ELIGIBLE",
    "INVALIDATED": "NOT_ELIGIBLE",
}


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _candidate_map(run: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("ticker") or "").strip().upper(): item
        for item in (run or {}).get("candidates") or []
        if str(item.get("ticker") or "").strip()
    }


def _status(candidate: dict[str, Any]) -> str | None:
    value = candidate.get("buyability_status") or candidate.get("decision_status")
    normalized = str(value).strip().upper() if value else None
    return LEGACY_STATUS_EQUIVALENTS.get(normalized or "", normalized)


def _distance(candidate: dict[str, Any]) -> Decimal | None:
    value = candidate.get("distance_to_trigger_pct")
    if value is None and candidate.get("pct_above_trigger") is not None:
        parsed = _decimal(candidate.get("pct_above_trigger"))
        return -parsed if parsed is not None else None
    return _decimal(value)


def _blockers(candidate: dict[str, Any]) -> set[str]:
    values = candidate.get("buy_conditions") or candidate.get("reasons") or []
    return {str(value).strip() for value in values if str(value).strip()}


def _gate_count(candidate: dict[str, Any], blockers: set[str]) -> int:
    value = candidate.get("remaining_gate_count")
    if isinstance(value, int) and value >= 0:
        return value
    return len(blockers)


def _stop(candidate: dict[str, Any]) -> Decimal | None:
    value = candidate.get("invalidation_price")
    plan = candidate.get("trade_plan")
    if value is None and isinstance(plan, dict):
        value = plan.get("stop") or plan.get("initial_stop")
    return _decimal(value)


def _evidence_key(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(item.get(field) or "").strip()
        for field in (
            "ticker",
            "evidence_type",
            "accession_number",
            "source_url",
            "published_at_utc",
        )
    )


def _evidence_category(item: dict[str, Any]) -> str:
    text = " ".join(
        str(item.get(field) or "")
        for field in ("evidence_type", "summary")
    ).lower()
    financing_words = (
        "atm",
        "at-the-market",
        "offering",
        "dilution",
        "warrant",
        "convertible",
    )
    if any(word in text for word in financing_words):
        return "dilution_or_financing"
    liquidity_words = ("liquidity", "cash runway", "working capital", "debt")
    if any(word in text for word in liquidity_words):
        return "liquidity"
    if any(word in text for word in ("10-k", "10-q", "8-k", "filing", "sec")):
        return "filing"
    return "other"


def _previous_run(
    session: Session, *, strategy_key: str, strategy_version: str, as_of_date: str
) -> dict[str, Any] | None:
    previous_date = (date.fromisoformat(as_of_date) - timedelta(days=1)).isoformat()
    listed = list_strategy_runs(
        session,
        strategy_key=strategy_key,
        strategy_version=strategy_version,
        run_type="as_run",
        end_date=previous_date,
        limit=1,
    )
    if not listed["items"]:
        return None
    return get_strategy_run(session, listed["items"][0]["run_id"])


def build_daily_changes(
    session: Session, *, payload: dict[str, Any]
) -> dict[str, Any]:
    """Compare a finalized alert with the latest earlier canonical run."""
    prior = _previous_run(
        session,
        strategy_key=str(payload["strategy_key"]),
        strategy_version=str(payload["strategy_version"]),
        as_of_date=str(payload["as_of_date"]),
    )
    current = _candidate_map(payload)
    previous = _candidate_map(prior)
    shared = sorted(current.keys() & previous.keys())

    classifications = []
    distances = []
    blockers = []
    stop_breaches = []
    fundamental_changes = []
    attention: dict[str, set[str]] = {}

    def attend(ticker: str, reason: str) -> None:
        attention.setdefault(ticker, set()).add(reason)

    for ticker in sorted(current.keys() - previous.keys()):
        attend(ticker, "new_candidate")

    for ticker in shared:
        now, before = current[ticker], previous[ticker]
        current_status, prior_status = _status(now), _status(before)
        if current_status != prior_status:
            old_priority = STATUS_PRIORITY.get(prior_status or "", 99)
            new_priority = STATUS_PRIORITY.get(current_status or "", 99)
            direction = "promoted" if new_priority < old_priority else "demoted"
            classifications.append(
                {
                    "ticker": ticker,
                    "previous": prior_status,
                    "current": current_status,
                    "direction": direction,
                }
            )
            attend(ticker, direction)

        old_distance, new_distance = _distance(before), _distance(now)
        if (
            old_distance is not None
            and new_distance is not None
            and old_distance != new_distance
        ):
            change = new_distance - old_distance
            distances.append(
                {
                    "ticker": ticker,
                    "previous_pct": str(old_distance),
                    "current_pct": str(new_distance),
                    "change_percentage_points": str(change),
                    "direction": "improved" if change < 0 else "deteriorated",
                }
            )
        if new_distance is not None and Decimal("0") <= new_distance <= Decimal("10"):
            reason = (
                "within_5_pct_of_trigger"
                if new_distance <= 5
                else "within_10_pct_of_trigger"
            )
            attend(ticker, reason)

        old_blockers, new_blockers = _blockers(before), _blockers(now)
        old_gate_count = _gate_count(before, old_blockers)
        new_gate_count = _gate_count(now, new_blockers)
        resolved = (
            sorted(old_blockers - new_blockers)
            if new_gate_count < old_gate_count
            else []
        )
        introduced = (
            sorted(new_blockers - old_blockers)
            if new_gate_count > old_gate_count
            else []
        )
        if resolved or introduced:
            blockers.append(
                {"ticker": ticker, "resolved": resolved, "introduced": introduced}
            )
            if resolved:
                attend(ticker, "blocker_resolved")
            if introduced:
                attend(ticker, "blocker_introduced")

        prior_stop = _stop(before)
        current_price = _decimal(
            now.get("current_price") or (now.get("metrics") or {}).get("close")
        )
        if (
            prior_stop is not None
            and current_price is not None
            and current_price <= prior_stop
        ):
            stop_breaches.append(
                {
                    "ticker": ticker,
                    "current_price": str(current_price),
                    "prior_stop": str(prior_stop),
                }
            )
            attend(ticker, "stop_breached")

        old_rs = _decimal(
            (before.get("metrics") or {}).get("relative_return_20d_vs_qqq_pct")
        )
        new_rs = _decimal(
            (now.get("metrics") or {}).get("relative_return_20d_vs_qqq_pct")
        )
        if old_rs is not None and new_rs is not None and old_rs <= 0 < new_rs:
            attend(ticker, "relative_strength_turned_positive")

        prior_metrics = before.get("metrics") or {}
        current_metrics = now.get("metrics") or {}
        for field, category in (
            ("latest_source_filing_date", "filing"),
            ("share_count_yoy_pct", "dilution"),
            ("cash_runway_months", "liquidity"),
            ("free_cash_flow_ttm", "liquidity"),
            ("current_ratio", "liquidity"),
        ):
            old_value = prior_metrics.get(field)
            new_value = current_metrics.get(field)
            if old_value is not None and new_value is not None and old_value != new_value:
                fundamental_changes.append(
                    {
                        "ticker": ticker,
                        "category": category,
                        "field": field,
                        "previous": old_value,
                        "current": new_value,
                    }
                )
                attend(ticker, category + "_data_changed")

    prior_evidence = {
        _evidence_key(item) for item in (prior or {}).get("evidence") or []
    }
    evidence_changes = []
    for item in payload.get("evidence") or []:
        category = _evidence_category(item)
        has_dated_source = bool(
            item.get("source_url")
            or item.get("accession_number")
            or item.get("published_at_utc")
        )
        if (
            category != "other"
            and has_dated_source
            and _evidence_key(item) not in prior_evidence
        ):
            ticker = str(item.get("ticker") or "").strip().upper()
            change = {
                "ticker": ticker,
                "category": category,
                "evidence_type": item.get("evidence_type"),
                "source_url": item.get("source_url"),
                "accession_number": item.get("accession_number"),
                "published_at_utc": item.get("published_at_utc"),
                "summary": item.get("summary"),
            }
            evidence_changes.append(change)
            if ticker:
                attend(ticker, "new_" + change["category"] + "_evidence")

    return {
        "baseline": prior is None,
        "previous_run_id": prior.get("run_id") if prior else None,
        "previous_as_of_date": prior.get("as_of_date") if prior else None,
        "new_candidates": sorted(current.keys() - previous.keys()),
        "removed_candidates": sorted(previous.keys() - current.keys()),
        "classification_changes": classifications,
        "trigger_distance_changes": distances,
        "stop_breaches": stop_breaches,
        "blocker_changes": blockers,
        "fundamental_changes": fundamental_changes,
        "evidence_changes": evidence_changes,
        "attention_today": [
            {"ticker": ticker, "reasons": sorted(reasons)}
            for ticker, reasons in sorted(attention.items())
        ],
    }


def render_daily_changes(changes: dict[str, Any]) -> str:
    lines = ["<!-- daily-changes:start -->", "## What changed since yesterday?"]
    if changes["baseline"]:
        return "\n".join(
            lines
            + [
                "",
                "- Baseline run: no earlier canonical alert is available.",
                "<!-- daily-changes:end -->",
            ]
        )
    entries: list[str] = []
    if changes["new_candidates"]:
        entries.append("New candidates: " + ", ".join(changes["new_candidates"]) + ".")
    for item in changes["classification_changes"]:
        entries.append(
            f'{item["ticker"]} {item["direction"]} from '
            f'{item["previous"]} to {item["current"]}.'
        )
    for item in changes["trigger_distance_changes"]:
        entries.append(
            f'{item["ticker"]} moved from {item["previous_pct"]}% to '
            f'{item["current_pct"]}% below trigger ({item["direction"]}).'
        )
    for item in changes["stop_breaches"]:
        entries.append(
            f'{item["ticker"]} breached its {item["prior_stop"]} prior stop '
            f'at {item["current_price"]}.'
        )
    for item in changes["blocker_changes"]:
        if item["resolved"]:
            entries.append(
                f'{item["ticker"]} resolved: '
                + "; ".join(value.rstrip(".") for value in item["resolved"])
                + "."
            )
        if item["introduced"]:
            entries.append(
                f'{item["ticker"]} introduced: '
                + "; ".join(value.rstrip(".") for value in item["introduced"])
                + "."
            )
    if changes.get("fundamental_changes"):
        tickers = sorted(
            {item["ticker"] for item in changes["fundamental_changes"]}
        )
        entries.append(
            "Filing, dilution, or liquidity data changed for: "
            + ", ".join(tickers)
            + "."
        )
    if changes["evidence_changes"]:
        tickers = sorted(
            {
                item["ticker"]
                for item in changes["evidence_changes"]
                if item["ticker"]
            }
        )
        entries.append("New evidence reviewed for: " + ", ".join(tickers) + ".")
    if not entries:
        entries.append(
            "No material candidate, classification, trigger, stop, blocker, or "
            "evidence changes."
        )
    return "\n".join(
        lines
        + [""]
        + ["- " + entry for entry in entries]
        + ["<!-- daily-changes:end -->"]
    )


def attach_daily_changes(report: str, changes: dict[str, Any]) -> str:
    """Prepend or replace the repository-owned daily-change section."""
    start_marker = "<!-- daily-changes:start -->"
    end_marker = "<!-- daily-changes:end -->"
    authored = report.strip()
    if start_marker in authored and end_marker in authored:
        before, remainder = authored.split(start_marker, 1)
        _old, after = remainder.split(end_marker, 1)
        authored = (before.strip() + "\n\n" + after.strip()).strip()
    return render_daily_changes(changes) + "\n\n" + authored
