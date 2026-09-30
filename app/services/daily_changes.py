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
EVENT_PRIORITY = {
    "BECAME_ACTIONABLE": 0,
    "ENTERED_NEAR_TRIGGER": 1,
    "LEFT_DISCOVERY_DUE_TO_RECOVERY": 2,
    "RECOVERY_PROGRESS": 3,
    "NEW_CANDIDATE": 4,
    "FUNDAMENTAL_CHANGE": 5,
    "RISK_CHANGE": 6,
    "STRUCTURAL_INVALIDATION": 7,
    "ARCHIVED": 8,
    "MINOR_CHANGE": 9,
    "NO_MATERIAL_CHANGE": 10,
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
    payload = candidate.get("payload")
    values = (
        candidate.get("blocker_ids")
        or (payload.get("blocker_ids") if isinstance(payload, dict) else None)
        or []
    )
    return {str(value).strip() for value in values if str(value).strip()}


def _stop(candidate: dict[str, Any]) -> Decimal | None:
    value = candidate.get("invalidation_price")
    plan = candidate.get("trade_plan")
    if value is None and isinstance(plan, dict):
        value = plan.get("stop") or plan.get("initial_stop")
    return _decimal(value)


def _score(candidate: dict[str, Any]) -> Decimal | None:
    return _decimal(candidate.get("setup_score", candidate.get("score")))


def _discovery_decline(candidate: dict[str, Any]) -> Decimal | None:
    return _decimal((candidate.get("metrics") or {}).get("price_change_12w_pct"))


def _recovery_is_meaningful_reason(before: dict[str, Any], now: dict[str, Any]) -> bool:
    """Recognize a recovery exit from the canonical candidate transition only."""
    before_payload = before.get("payload") if isinstance(before.get("payload"), dict) else {}
    now_payload = now.get("payload") if isinstance(now.get("payload"), dict) else {}
    if not (
        before_payload.get("in_raw_pool") is True
        and now_payload.get("in_raw_pool") is False
        and str(before.get("screen_bucket") or "").lower() == "qualified"
        and str(now.get("screen_bucket") or "").lower() == "dropped"
    ):
        return False
    presentation = now_payload.get("presentation") if isinstance(now_payload.get("presentation"), dict) else {}
    presentation_blockers = presentation.get("blockers") if isinstance(presentation, dict) else []
    blocker_text = [
        " ".join(str(blocker.get(key) or "") for key in ("gate", "reason", "clear_condition")).lower()
        for blocker in presentation_blockers or []
        if isinstance(blocker, dict)
    ]
    decline_failure = any("12-week price change" in text or "decline screen" in text for text in blocker_text)
    independent_screen_failure = any(
        text.startswith("screen:") and "12-week price change" not in text and "decline screen" not in text
        for text in blocker_text
    )
    independent_risk_failure = bool(now.get("deterministic_risk_flags")) or any(
        text.startswith("deterministic risk review:") or text.startswith("risk review:")
        for text in blocker_text
    )
    return decline_failure and not independent_screen_failure and not independent_risk_failure


def _is_already_breached(candidate: dict[str, Any]) -> bool:
    stop = _stop(candidate)
    price = _decimal(candidate.get("current_price") or (candidate.get("metrics") or {}).get("close"))
    return stop is not None and price is not None and price <= stop


def _materially_changed_level(previous: Any, current: Any) -> bool:
    old_level, new_level = _decimal(previous), _decimal(current)
    if old_level is None or new_level is None:
        return old_level != new_level
    return abs(new_level - old_level) >= max(abs(old_level) * Decimal("0.01"), Decimal("0.01"))


def _report_candidate(candidate: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    metrics = candidate.get("metrics") or {}
    payload = candidate.get("payload") or {}
    presentation = payload.get("presentation") if isinstance(payload, dict) else {}
    event_types = set(event.get("event_types") or [event.get("event_type")])
    conditions = [str(value) for value in candidate.get("buy_conditions") or [] if value]
    condition_text = " ".join(conditions).lower()
    if "LEFT_DISCOVERY_DUE_TO_RECOVERY" in event_types:
        next_step = "Continue tracking the recovery; watch for breakout confirmation and remaining entry gates."
    elif "ENTERED_NEAR_TRIGGER" in event_types:
        next_step = "Confirm breakout above the current trigger."
    elif "RISK_CHANGE" in event_types or any(word in condition_text for word in ("financing", "dilution")):
        next_step = "Resolve financing/dilution risk before entry."
    elif "market gate" in condition_text or candidate.get("market_regime_gate_passed") is False:
        next_step = "Wait for the market gate."
    elif "volume" in condition_text:
        next_step = "Wait for volume confirmation."
    elif "ema20" in condition_text or "ema 20" in condition_text:
        next_step = "Reclaim EMA20."
    elif isinstance(presentation, dict) and presentation.get("next"):
        next_step = presentation["next"]
    else:
        next_step = conditions[0] if conditions else "Continue monitoring the saved setup conditions."
    return {
        "ticker": candidate.get("ticker"),
        "status": candidate.get("buyability_status") or candidate.get("decision_status"),
        "current_price": candidate.get("current_price") or metrics.get("close"),
        "trigger": candidate.get("trigger_price"),
        "distance_to_trigger_pct": candidate.get("distance_to_trigger_pct"),
        "relative_strength_20d": metrics.get("relative_return_20d_vs_qqq_pct"),
        "event_type": event["event_type"],
        "event_types": event["event_types"],
        "what_changed": event["text"],
        "what_matters_next": str(next_step),
    }


def _research_scope_only_status_change(before: dict[str, Any], now: dict[str, Any]) -> bool:
    """Avoid treating an absent refresh as a stock-specific downgrade."""
    old_payload = before.get("payload") if isinstance(before.get("payload"), dict) else {}
    new_payload = now.get("payload") if isinstance(now.get("payload"), dict) else {}
    old_evidence = old_payload.get("qualitative_evidence_status")
    new_evidence = new_payload.get("qualitative_evidence_status")
    if old_evidence not in {"fresh_researched", "reused_current"} or new_evidence not in {"missing", "stale_researched"}:
        return False
    # The status is presentation-only when the observed setup and all
    # deterministic gates are otherwise unchanged.
    fields = (
        "distance_to_trigger_pct", "current_price", "trigger_price",
        "invalidation_price", "setup_score", "score", "technical_gate_passed",
        "market_regime_gate_passed", "remaining_gate_count",
    )
    return (
        all(before.get(field) == now.get(field) for field in fields)
        and before.get("metrics") == now.get("metrics")
        and _blockers(before) == _blockers(now)
    ) or bool(old_evidence and new_evidence)


def _format_pct(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'))}%"


def _format_money(value: Any) -> str:
    parsed = _decimal(value)
    return f"${parsed.quantize(Decimal('0.01'))}" if parsed is not None else "—"


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
    session: Session, *, strategy_key: str, as_of_date: str
) -> dict[str, Any] | None:
    previous_date = (date.fromisoformat(as_of_date) - timedelta(days=1)).isoformat()
    listed = list_strategy_runs(
        session,
        strategy_key=strategy_key,
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
    recovery_exits = []
    events: list[dict[str, Any]] = []
    attention: dict[str, set[str]] = {}

    def attend(ticker: str, reason: str) -> None:
        attention.setdefault(ticker, set()).add(reason)

    for ticker in sorted(current.keys() - previous.keys()):
        attend(ticker, "new_candidate")
        events.append({
            "ticker": ticker,
            "event_type": "NEW_CANDIDATE",
            "text": f"{ticker} entered the tracked set.",
        })

    for ticker in shared:
        now, before = current[ticker], previous[ticker]
        current_status, prior_status = _status(now), _status(before)
        prior_decline, current_decline = _discovery_decline(before), _discovery_decline(now)
        recovery_exit = (
            prior_decline is not None
            and current_decline is not None
            and prior_decline <= Decimal("-20")
            and current_decline > Decimal("-20")
            and _recovery_is_meaningful_reason(before, now)
        )
        if recovery_exit:
            recovery_exits.append({
                "ticker": ticker,
                "previous_decline_pct": str(prior_decline),
                "current_decline_pct": str(current_decline),
            })
            attend(ticker, "left_discovery_due_to_recovery")
            events.append({
                "ticker": ticker,
                "event_type": "LEFT_DISCOVERY_DUE_TO_RECOVERY",
                "text": (
                    f"{ticker} recovered out of the original 12-week decline screen "
                    f"({_format_pct(prior_decline)} to {_format_pct(current_decline)})."
                ),
            })
        if current_status != prior_status and not _research_scope_only_status_change(before, now):
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
            if current_status in {"BUY_NOW", "ALMOST_READY"} and prior_status not in {"BUY_NOW", "ALMOST_READY"}:
                events.append({
                    "ticker": ticker,
                    "event_type": "BECAME_ACTIONABLE",
                    "text": f"{ticker} became {current_status}.",
                })

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
            if change <= Decimal("-0.5"):
                events.append({
                    "ticker": ticker,
                    "event_type": "RECOVERY_PROGRESS",
                    "text": f"{ticker} improved from {_format_pct(old_distance)} to {_format_pct(new_distance)} below trigger.",
                })
        if new_distance is not None and Decimal("0") <= new_distance <= Decimal("10"):
            reason = (
                "within_5_pct_of_trigger"
                if new_distance <= 5
                else "within_10_pct_of_trigger"
            )
            attend(ticker, reason)
            if old_distance is None or old_distance > Decimal("10"):
                events.append({
                    "ticker": ticker,
                    "event_type": "ENTERED_NEAR_TRIGGER",
                    "text": f"{ticker} entered the near-trigger range at {_format_pct(new_distance)} below trigger.",
                })

        old_blockers, new_blockers = _blockers(before), _blockers(now)
        resolved = sorted(old_blockers - new_blockers)
        introduced = sorted(new_blockers - old_blockers)
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
            and (
                not _is_already_breached(before)
                or _materially_changed_level(
                    prior_stop, now.get("invalidation_price")
                )
            )
        ):
            stop_breaches.append(
                {
                    "ticker": ticker,
                    "current_price": str(current_price),
                    "prior_stop": str(prior_stop),
                }
            )
            attend(ticker, "stop_breached")
            events.append({
                "ticker": ticker,
                "event_type": "STRUCTURAL_INVALIDATION",
                "text": f"{ticker} breached its setup invalidation level at {current_price}.",
            })

        old_rs = _decimal(
            (before.get("metrics") or {}).get("relative_return_20d_vs_qqq_pct")
        )
        new_rs = _decimal(
            (now.get("metrics") or {}).get("relative_return_20d_vs_qqq_pct")
        )
        if old_rs is not None and new_rs is not None and old_rs <= 0 < new_rs:
            attend(ticker, "relative_strength_turned_positive")
            events.append({
                "ticker": ticker,
                "event_type": "RECOVERY_PROGRESS",
                "text": f"{ticker} relative strength turned positive at {_format_pct(new_rs)}.",
            })

        old_risk_flags = set(before.get("deterministic_risk_flags") or [])
        new_risk_flags = set(now.get("deterministic_risk_flags") or [])
        if old_risk_flags != new_risk_flags:
            events.append({
                "ticker": ticker,
                "event_type": "RISK_CHANGE",
                "text": f"{ticker} deterministic risk flags changed.",
            })

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
                events.append({
                    "ticker": ticker,
                    "event_type": "FUNDAMENTAL_CHANGE",
                    "text": f"{ticker} had a material {category} data change.",
                })
        before_score, current_score = _score(before), _score(now)
        if before_score is not None and current_score is not None and current_score > before_score and current_score - before_score >= 3:
            events.append({
                "ticker": ticker,
                "event_type": "RECOVERY_PROGRESS",
                "text": f"{ticker} setup score improved from {before_score:.0f} to {current_score:.0f}.",
            })

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
                events.append({
                    "ticker": ticker,
                    "event_type": "RISK_CHANGE" if change["category"] in {"dilution_or_financing", "liquidity"} else "FUNDAMENTAL_CHANGE",
                    "text": f"{ticker} had new {change['category'].replace('_', ' ')} evidence.",
                })

    for ticker in sorted(previous.keys() - current.keys()):
        events.append({
            "ticker": ticker,
            "event_type": "ARCHIVED",
            "text": f"{ticker} was archived because it no longer met the tracked screen.",
        })

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_ticker.setdefault(str(event["ticker"]), []).append(event)
    consolidated_events: list[dict[str, Any]] = []
    for ticker, ticker_events in by_ticker.items():
        unique_types = sorted(
            {str(item["event_type"]) for item in ticker_events},
            key=lambda value: EVENT_PRIORITY.get(value, 10),
        )
        texts = list(dict.fromkeys(str(item["text"]) for item in ticker_events))
        consolidated_events.append({
            "ticker": ticker,
            "event_type": unique_types[0],
            "event_types": unique_types,
            "text": " ".join(texts),
        })
    consolidated_events.sort(
        key=lambda item: (EVENT_PRIORITY.get(item["event_type"], 10), item["ticker"])
    )

    report_candidates = {**previous, **current}
    report_items = [
        _report_candidate(report_candidates[item["ticker"]], item)
        for item in consolidated_events
        if item["ticker"] in report_candidates
    ]
    attention_types = {"BECAME_ACTIONABLE", "ENTERED_NEAR_TRIGGER", "LEFT_DISCOVERY_DUE_TO_RECOVERY"}
    def report_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        ticker = str(item["ticker"])
        now, before = current.get(ticker, previous.get(ticker, {})), previous.get(ticker, {})
        distance = _distance(now)
        old_distance = _distance(before)
        improvement = (old_distance - distance) if old_distance is not None and distance is not None else Decimal("-999999")
        rs = _decimal((now.get("metrics") or {}).get("relative_return_20d_vs_qqq_pct"))
        old_rs = _decimal((before.get("metrics") or {}).get("relative_return_20d_vs_qqq_pct"))
        rs_improvement = (rs - old_rs) if rs is not None and old_rs is not None else Decimal("-999999")
        score = _score(now)
        return (
            EVENT_PRIORITY.get(str(item["event_type"]), 10),
            abs(distance) if distance is not None else Decimal("999999"),
            -improvement,
            -(rs if rs is not None else Decimal("-999999")),
            -rs_improvement,
            -(score if score is not None else Decimal("-999999")),
            ticker,
        )

    attention_items = sorted(
        (item for item in report_items if item["event_type"] in attention_types),
        key=report_sort_key,
    )[:8]
    attention_tickers = {item["ticker"] for item in attention_items}
    recovery_watch = [
        item for item in report_items
        if item["ticker"] not in attention_tickers
        and set(item["event_types"]) & {"LEFT_DISCOVERY_DUE_TO_RECOVERY", "RECOVERY_PROGRESS"}
    ]
    recovery_watch.sort(key=report_sort_key)
    material = [
        item for item in report_items
        if item["ticker"] not in attention_tickers
        and item["ticker"] not in {entry["ticker"] for entry in recovery_watch}
    ]

    meaningful: list[dict[str, str | int]] = []
    for ticker in sorted(current.keys() - previous.keys()):
        meaningful.append({"category": "New", "ticker": ticker, "text": f"{ticker} entered the tracked set.", "priority": 3, "event_type": "NEW_CANDIDATE"})
    for ticker in sorted(previous.keys() - current.keys()):
        meaningful.append({"category": "Removed", "ticker": ticker, "text": f"{ticker} was removed because it no longer met the tracked screen.", "priority": 3, "event_type": "ARCHIVED"})
    for item in consolidated_events:
        if item["event_type"] == "NEW_CANDIDATE":
            continue
        meaningful.append({
            "category": item["event_type"].replace("_", " ").title(),
            "ticker": item["ticker"],
            "text": item["text"],
            "priority": EVENT_PRIORITY[item["event_type"]],
            "event_type": item["event_type"],
        })
    for item in classifications:
        direction = "Improved" if item["direction"] == "promoted" else "Deteriorated"
        meaningful.append({"category": direction, "ticker": item["ticker"], "text": f"{item['ticker']} moved from {item['previous']} to {item['current']}.", "priority": 3, "event_type": "RECOVERY_PROGRESS" if item["direction"] == "promoted" else "MINOR_CHANGE"})
    for item in distances:
        change = _decimal(item["change_percentage_points"])
        if change is not None and abs(change) >= Decimal("0.5"):
            direction = "Improved" if change < 0 else "Deteriorated"
            meaningful.append({
                "category": direction,
                "ticker": item["ticker"],
                "text": f"{item['ticker']} {direction.lower()} from {_format_pct(_decimal(item['previous_pct']) or Decimal(0))} to {_format_pct(_decimal(item['current_pct']) or Decimal(0))} below trigger.",
                "priority": 2,
                "event_type": "RECOVERY_PROGRESS" if change < 0 else "MINOR_CHANGE",
            })
    for ticker in shared:
        before_score, current_score = _score(previous[ticker]), _score(current[ticker])
        if before_score is not None and current_score is not None and abs(current_score - before_score) >= 3:
            direction = "Improved" if current_score > before_score else "Deteriorated"
            meaningful.append({
                "category": direction,
                "ticker": ticker,
                "text": f"{ticker} setup score {direction.lower()} from {before_score:.0f} to {current_score:.0f}.",
                "priority": 2,
                "event_type": "RECOVERY_PROGRESS" if direction == "Improved" else "MINOR_CHANGE",
            })
    for item in stop_breaches:
        meaningful.append({"category": "Deteriorated", "ticker": item["ticker"], "text": f"{item['ticker']} breached its setup invalidation level at {item['current_price']}.", "priority": 4, "event_type": "STRUCTURAL_INVALIDATION"})
    meaningful.sort(key=lambda item: (EVENT_PRIORITY.get(str(item.get("event_type")), 10), str(item["ticker"]), str(item["text"])))

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
        "recovery_exits": recovery_exits,
        "events": consolidated_events,
        "report_sections": {
                "needs_attention_today": attention_items,
            "recovery_watch": recovery_watch,
            "new_materially_changed": material,
        },
        "attention_today": [
                {"ticker": ticker, "reasons": sorted(reasons)}
            for ticker, reasons in sorted(attention.items())
        ],
        "meaningful_changes": meaningful[:8],
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
    sections = changes.get("report_sections") or {}
    if any(sections.get(name) for name in ("needs_attention_today", "recovery_watch", "new_materially_changed")):
        def render_section(title: str, entries: list[dict[str, Any]]) -> list[str]:
            if not entries:
                return []
            result = [f"### {title}", "", "| Ticker | Status | Current | Trigger | Distance | 20d RS | What changed | What matters next |", "|---|---|---:|---:|---:|---:|---|---|"]
            for item in entries:
                result.append(
                    "| " + " | ".join(
                        (
                            str(item.get("ticker") or "—"),
                            str(item.get("status") or "—"),
                            _format_money(item.get("current_price")),
                            _format_money(item.get("trigger")),
                            _format_pct(_decimal(item.get("distance_to_trigger_pct"))) if _decimal(item.get("distance_to_trigger_pct")) is not None else "—",
                            _format_pct(_decimal(item.get("relative_strength_20d"))) if _decimal(item.get("relative_strength_20d")) is not None else "—",
                            str(item.get("what_changed") or "—"),
                            str(item.get("what_matters_next") or "—"),
                        )
                    ) + " |"
                )
            return result + [""]

        rendered: list[str] = []
        rendered.extend(render_section("Needs attention today", sections.get("needs_attention_today") or []))
        rendered.extend(render_section("Recovery Watch", sections.get("recovery_watch") or []))
        rendered.extend(render_section("New / materially changed", sections.get("new_materially_changed") or []))
        if not rendered:
            rendered.extend(["No other material changes.", ""])
        return "\n".join(lines + [""] + rendered + ["<!-- daily-changes:end -->"])
    if changes.get("meaningful_changes") is not None:
        entries = [str(item["text"]) for item in changes["meaningful_changes"]]
        if not entries:
            entries.append("No material setup changes since the previous alert.")
        return "\n".join(lines + [""] + ["- " + entry for entry in entries] + ["<!-- daily-changes:end -->"])
    entries: list[str] = []
    if changes["new_candidates"]:
        entries.append("New candidates: " + ", ".join(changes["new_candidates"]) + ".")
    if changes.get("removed_candidates"):
        entries.append(
            "Removed candidates: " + ", ".join(changes["removed_candidates"]) + "."
        )
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
        entries.append("No material setup changes since the previous alert.")
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
