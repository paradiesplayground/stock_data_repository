"""Server-side v0.9 shadow alert: computed and persisted beside v0.8, never delivered.

The job evaluates every name in today's screen plus every name v0.9 is still
tracking, with the deterministic engine in ``alert_v09_engine``. It reuses the
research the live alert already saved (read-only), keeps its own lifecycle rows
under ``LIFECYCLE_KEY`` so v0.8 state is never touched, and records one
``run_type="shadow"`` strategy run per market date under its own strategy key.
It never publishes or emails.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.decision_contract import V09_DECISION_CONTRACT_VERSION
from app.mcp_queries import get_data_freshness, get_security_features, query_security_features
from app.models import (
    DailyAlertPreparation,
    DailyAlertPreparationResearch,
    DailyPriceBar,
    StrategyCandidateLifecycle,
    StrategyCandidateLifecycleObservation,
    StrategyRun,
)
from app.services.alert_v09_engine import (
    SECTION_BLOCKED,
    SECTION_BUY,
    SECTION_CLOSE,
    SECTION_EXITED,
    SECTION_RECOVERING,
    EngineConfig,
    Research,
    evaluate,
    load_configuration,
)
from app.services.blocker_codes import blocker_codes
from app.services.daily_stock_alert_preparation import _spy_market_regime
from app.services.strategy_tracking import configuration_fingerprint, record_strategy_run

logger = logging.getLogger(__name__)

SHADOW_STRATEGY_KEY = "dynamic_swing_buy_alerts_shadow"
LIVE_STRATEGY_KEY = "dynamic_swing_buy_alerts"
LIFECYCLE_KEY = "dynamic_swing_buy_alerts@0.9"
CONFIGURATION = load_configuration()
STRATEGY_VERSION = CONFIGURATION["strategy"]["version"]
FEATURE_VERSION = CONFIGURATION["feature_calculation_version"]
EXCLUDED_INDUSTRY_GROUPS = CONFIGURATION["universe"]["exclude_industry_groups"]
UNIVERSE_LIMIT = 500
RECENT_SECTIONS_KEPT = 6
SECTION_ORDER = (SECTION_BUY, SECTION_CLOSE, SECTION_RECOVERING, SECTION_BLOCKED)


def _sessions_counter(session_dates: list[date]) -> Callable[[date, date], int]:
    """Count stored market sessions in (start, end]."""
    def count(start: date, end: date) -> int:
        if end <= start:
            return 0
        return bisect_right(session_dates, end) - bisect_right(session_dates, start)
    return count


def _session_dates(session: Session, end: date, lookback_days: int = 220) -> list[date]:
    return list(session.scalars(
        select(DailyPriceBar.trade_date)
        .where(
            DailyPriceBar.ticker == "QQQ",
            DailyPriceBar.trade_date.between(end - timedelta(days=lookback_days), end),
        )
        .distinct()
        .order_by(DailyPriceBar.trade_date)
    ).all())


def _research_by_ticker(session: Session, as_of_date: date, max_age_days: int) -> dict[str, Research]:
    """Latest saved research per ticker as known on the market date (point in time)."""
    rows = session.execute(
        select(DailyAlertPreparation.as_of_date, DailyAlertPreparationResearch)
        .join(
            DailyAlertPreparation,
            DailyAlertPreparation.preparation_id == DailyAlertPreparationResearch.preparation_id,
        )
        .where(DailyAlertPreparation.as_of_date.between(
            as_of_date - timedelta(days=max_age_days), as_of_date
        ))
        .order_by(DailyAlertPreparation.as_of_date, DailyAlertPreparationResearch.updated_at_utc)
    ).all()
    latest: dict[str, Research] = {}
    for research_date, research in rows:
        latest[research.ticker.upper()] = Research(
            as_of_date=research_date,
            blocker_codes=tuple(blocker_codes(research.qualitative_blockers or [])),
        )
    return latest


def _universe(session: Session, as_of_date: date) -> dict[str, dict[str, Any]]:
    """Names that pass the size, growth, liquidity, and listing gates today."""
    thresholds = CONFIGURATION["hard_thresholds"]
    universe = CONFIGURATION["universe"]
    pool = query_security_features(
        session,
        as_of_date=as_of_date.isoformat(),
        min_price=universe["minimum_price"],
        min_market_cap=universe["minimum_market_cap"],
        min_ttm_revenue_growth_pct=thresholds["minimum_ttm_revenue_growth_pct"],
        min_quarter_revenue_growth_pct=thresholds["minimum_quarter_revenue_growth_pct"],
        min_avg_dollar_volume_20d=thresholds["minimum_avg_dollar_volume_20d"],
        exclude_industry_groups=EXCLUDED_INDUSTRY_GROUPS,
        limit=UNIVERSE_LIMIT,
        calculation_version=FEATURE_VERSION,
    )
    if pool.get("as_of_date") != as_of_date.isoformat():
        raise RuntimeError(f"feature calculation {FEATURE_VERSION} is not available for {as_of_date}")
    if pool["count"] >= UNIVERSE_LIMIT:
        raise RuntimeError("base-gate universe reached the query limit; raise UNIVERSE_LIMIT")
    return {item["ticker"]: item for item in pool["items"]}


def _tracked_feature(session: Session, ticker: str, as_of_date: date) -> dict[str, Any] | None:
    result = get_security_features(
        session, ticker, as_of_date=as_of_date.isoformat(), calculation_version=FEATURE_VERSION
    )
    item = result.get("item") if isinstance(result, dict) else None
    if not item or item.get("as_of_date") != as_of_date.isoformat() or item.get("price_date") != as_of_date.isoformat():
        return None
    return item


def _unchanged(recent: list[list[Any]], collapse_sessions: int) -> bool:
    window = recent[-(collapse_sessions + 1):]
    if len(window) < collapse_sessions + 1 or len({entry[1] for entry in window}) != 1:
        return False
    first, last = window[0][2], window[-1][2]
    return first is not None and last is not None and abs(Decimal(first) - Decimal(last)) < 1


def email_sections(candidates: list[dict[str, Any]], configuration: dict[str, Any]) -> dict[str, Any]:
    """Apply the email caps and the unchanged-name collapse to the ordered sections."""
    caps = configuration["email"]
    limits = {SECTION_BUY: caps["max_buy"], SECTION_CLOSE: caps["max_close"], SECTION_RECOVERING: caps["max_recovering"]}

    def sort_key(item: dict[str, Any]) -> tuple:
        section = item["payload"]["section"]
        distance = Decimal(item["distance_to_trigger_pct"]) if item.get("distance_to_trigger_pct") else Decimal("999")
        rs = Decimal(str(item["metrics"].get("relative_return_20d_vs_qqq_pct") or "-999"))
        if section == SECTION_BUY:
            return (-rs, item["ticker"])
        if section == SECTION_RECOVERING:
            return (item["technical_state"] == "broken", abs(distance), -rs, item["ticker"])
        return (distance, -rs, item["ticker"])

    sections: dict[str, Any] = {}
    for section in (SECTION_BUY, SECTION_CLOSE, SECTION_RECOVERING):
        members = sorted((item for item in candidates if item["payload"]["section"] == section), key=sort_key)
        quiet = [item["ticker"] for item in members if item["payload"].get("unchanged")]
        active = [item["ticker"] for item in members if not item["payload"].get("unchanged")]
        sections[section] = {
            "shown": active[: limits[section]],
            "more_on_website": max(0, len(active) - limits[section]),
            "unchanged": quiet,
        }
    blocked = [item for item in candidates if item["payload"]["section"] == SECTION_BLOCKED]
    sections[SECTION_BLOCKED] = {
        "count": len(blocked),
        "newly_blocked": sorted(item["ticker"] for item in blocked if item["payload"].get("section_changed")),
    }
    return sections


def render_markdown(as_of_date: date, market_ok: bool, candidates: list[dict[str, Any]], sections: dict[str, Any]) -> str:
    by_ticker = {item["ticker"]: item for item in candidates}
    lines = [f"# Daily Stock Alert v0.9 (SHADOW) - {as_of_date.isoformat()}", "",
             "Shadow run: not delivered and not advice.",
             "Market gate: " + ("PASS" if market_ok else "BLOCKED - setups listed for awareness only"), ""]
    titles = {SECTION_BUY: "Buy today", SECTION_CLOSE: "Close (within 5% of trigger)", SECTION_RECOVERING: "Recovering"}
    for section in (SECTION_BUY, SECTION_CLOSE, SECTION_RECOVERING):
        detail = sections[section]
        lines.append(f"## {titles[section]}")
        if not detail["shown"] and not detail["unchanged"]:
            lines.append("- none")
        for ticker in detail["shown"]:
            item = by_ticker[ticker]
            lines.append(f"- **{ticker}** - {item['status_reason']} Next: {item['buy_conditions'][0]}")
        if detail["more_on_website"]:
            lines.append(f"- +{detail['more_on_website']} more in the full record")
        if detail["unchanged"]:
            lines.append("- Unchanged: " + ", ".join(detail["unchanged"]))
        lines.append("")
    blocked = sections[SECTION_BLOCKED]
    lines.append(f"## Blocked: {blocked['count']}" + (
        " (newly blocked: " + ", ".join(blocked["newly_blocked"]) + ")" if blocked["newly_blocked"] else ""))
    return "\n".join(lines)


def _live_comparison(session: Session, as_of_date: date) -> dict[str, Any] | None:
    from app.services.strategy_tracking import get_strategy_run, list_strategy_runs

    listed = list_strategy_runs(
        session, strategy_key=LIVE_STRATEGY_KEY, run_type="as_run",
        start_date=as_of_date.isoformat(), end_date=as_of_date.isoformat(), limit=1,
    )
    if not listed["items"]:
        return None
    run = get_strategy_run(session, listed["items"][0]["run_id"])
    statuses: dict[str, list[str]] = {}
    for item in run.get("candidates") or []:
        status = str(((item.get("payload") or {}).get("setup_buyability_status")) or item.get("buyability_status") or "")
        statuses.setdefault(status, []).append(item["ticker"])
    return {"run_id": run.get("run_id"), "setup_statuses": statuses}


def advance_day(
    items: dict[str, dict[str, Any]],
    listed: set[str],
    states: dict[str, dict[str, Any] | None],
    *,
    as_of_date: date,
    market_ok: bool,
    research: dict[str, Research],
    sessions_between: Callable[[date, date], int],
    config: EngineConfig,
    configuration: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[tuple[str, str | None, str]]]:
    """Evaluate one market date for every screened or tracked name (pure).

    ``items`` holds the day's features for the base-gate universe plus every
    tracked name; ``listed`` is the subset that passed the listing and base
    gates. Returns the candidates, the next state per evaluated ticker, and the
    section transitions.
    """
    candidates: list[dict[str, Any]] = []
    next_states: dict[str, dict[str, Any]] = {}
    transitions: list[tuple[str, str | None, str]] = []
    collapse = configuration["email"]["collapse_unchanged_sessions"]
    for ticker in sorted(items):
        state = states.get(ticker)
        candidate, next_state = evaluate(
            items[ticker], state, as_of_date=as_of_date, listing_ok=ticker in listed,
            market_gate_passed=market_ok, research=research.get(ticker),
            sessions_between=sessions_between, config=config,
        )
        if next_state is None:
            continue
        previous_section = state.get("section") if state and not state.get("exited") else None
        section = next_state.get("section") or SECTION_EXITED
        recent = list(state.get("recent") or []) if previous_section else []
        recent.append([as_of_date.isoformat(), section, candidate.get("distance_to_trigger_pct") if candidate else None])
        next_state["recent"] = recent[-RECENT_SECTIONS_KEPT:]
        if candidate is not None:
            candidate["payload"]["unchanged"] = _unchanged(next_state["recent"], collapse)
            candidate["payload"]["section_changed"] = section != previous_section
            candidate["payload"]["previous_section"] = previous_section
            candidates.append(candidate)
        if section != previous_section:
            transitions.append((ticker, previous_section, section))
        next_states[ticker] = next_state
    return candidates, next_states, transitions


def run_daily_stock_alert_shadow(
    session: Session,
    settings: Settings | None = None,
    *,
    as_of_date: date | None = None,
    record_run: bool = True,
    replayed: bool = False,
) -> dict[str, Any]:
    """Advance v0.9 tracking for one market date and record the shadow run (idempotent per date)."""
    if as_of_date is None:
        freshness = get_data_freshness(session, settings)
        if not freshness.get("ready_for_screening"):
            raise RuntimeError("data is not ready for screening")
        as_of_date = date.fromisoformat(str(freshness["expected_market_date"]))
    idempotency_key = f"{SHADOW_STRATEGY_KEY}:{STRATEGY_VERSION}:{as_of_date.isoformat()}:{FEATURE_VERSION}"
    existing_run = session.scalar(select(StrategyRun).where(StrategyRun.idempotency_key == idempotency_key))
    rows = {
        row.ticker: row for row in session.scalars(
            select(StrategyCandidateLifecycle).where(StrategyCandidateLifecycle.strategy_key == LIFECYCLE_KEY)
        ).all()
    }
    processed = {
        (row.metadata_json or {}).get("v09_state", {}).get("last_processed_date") for row in rows.values()
    } - {None}
    latest_processed = max(processed) if processed else None
    if existing_run is not None or (latest_processed == as_of_date.isoformat() and not record_run):
        return {"status": "already_processed", "as_of_date": as_of_date.isoformat(),
                "run_id": existing_run.run_id if existing_run else None}
    if latest_processed is not None and latest_processed >= as_of_date.isoformat():
        # Tracking state is cumulative, so a date can only be evaluated once and in order.
        raise ValueError(f"v0.9 tracking has already advanced to {latest_processed}; cannot evaluate {as_of_date}")

    config = EngineConfig.from_configuration(CONFIGURATION)
    sessions_between = _sessions_counter(_session_dates(session, as_of_date))
    regime = _spy_market_regime(session, as_of_date)
    market_ok = bool(regime["gate_passed"])
    research = _research_by_ticker(session, as_of_date, config.research_max_age_days)
    universe = _universe(session, as_of_date)

    active_tracked = {
        ticker for ticker, row in rows.items()
        if not (row.metadata_json or {}).get("v09_state", {}).get("exited", True)
    }
    items: dict[str, dict[str, Any]] = dict(universe)
    missing: list[str] = []
    for ticker in sorted(active_tracked - set(universe)):
        item = _tracked_feature(session, ticker, as_of_date)
        if item is None:
            missing.append(ticker)
        else:
            items[ticker] = item
    states = {
        ticker: (row.metadata_json or {}).get("v09_state") for ticker, row in rows.items()
    }
    candidates, next_states, transitions = advance_day(
        items, set(universe), states, as_of_date=as_of_date, market_ok=market_ok,
        research=research, sessions_between=sessions_between, config=config,
        configuration=CONFIGURATION,
    )
    by_ticker = {item["ticker"]: item for item in candidates}
    for ticker, state in next_states.items():
        _store_lifecycle(session, rows, ticker, as_of_date, state, by_ticker.get(ticker))

    for ticker, previous, section in transitions:
        session.add(StrategyCandidateLifecycleObservation(
            strategy_key=LIFECYCLE_KEY, ticker=ticker, observation_date=as_of_date,
            from_state=(previous or "").upper() or None, to_state=section.upper(),
            event=f"{(previous or 'NEW').upper()}_TO_{section.upper()}", outcome_status=section.upper(),
            metrics={}, source_run_id=None,
        ))

    sections = email_sections(candidates, CONFIGURATION)
    research_queue = [
        item["ticker"] for item in candidates
        if item["payload"].get("needs_research") and item["technical_state"] in {"confirmed", "near_trigger"}
        and Decimal(str(item["metrics"].get("relative_return_20d_vs_qqq_pct") or "-1")) > 0
    ]
    summary = {
        "shadow": True,
        "replayed": replayed,
        "market_regime": regime,
        "section_counts": {
            section: sum(1 for item in candidates if item["payload"]["section"] == section)
            for section in (*SECTION_ORDER, SECTION_EXITED, "incomplete")
        },
        "email_sections": sections,
        "research_queue": research_queue,
        "missing_feature_tickers": missing,
        "tracked_count": len([item for item in candidates if item["payload"]["section"] != SECTION_EXITED]),
        "live_v08_comparison": _live_comparison(session, as_of_date),
        "lifecycle_key": LIFECYCLE_KEY,
    }
    if not record_run:
        session.commit()
        return {"status": "advanced", "as_of_date": as_of_date.isoformat(), "summary": summary}

    result = record_strategy_run(
        session,
        strategy_key=SHADOW_STRATEGY_KEY,
        strategy_version=STRATEGY_VERSION,
        strategy_name="Dynamic swing buy alerts (v0.9 shadow)",
        as_of_date=as_of_date.isoformat(),
        run_type="shadow",
        idempotency_key=idempotency_key,
        configuration=CONFIGURATION,
        filters={"exclude_industry_groups": EXCLUDED_INDUSTRY_GROUPS, "lifecycle_key": LIFECYCLE_KEY},
        candidates=candidates,
        summary=summary,
        report_markdown=render_markdown(as_of_date, market_ok, candidates, sections),
        feature_calculation_version=FEATURE_VERSION,
        decision_contract_version=V09_DECISION_CONTRACT_VERSION,
        notes=f"v0.9 shadow; configuration {configuration_fingerprint(CONFIGURATION)}",
        publish=False,
    )
    return {"status": "recorded", "as_of_date": as_of_date.isoformat(), "run_id": result["run_id"],
            "section_counts": summary["section_counts"], "research_queue": research_queue}


def _store_lifecycle(
    session: Session,
    rows: dict[str, StrategyCandidateLifecycle],
    ticker: str,
    as_of_date: date,
    state: dict[str, Any],
    candidate: dict[str, Any] | None,
) -> None:
    row = rows.get(ticker)
    section = (state.get("section") or SECTION_EXITED).upper()
    if row is None:
        row = StrategyCandidateLifecycle(
            strategy_key=LIFECYCLE_KEY, ticker=ticker, first_discovered_date=as_of_date,
            lifecycle_state=section, lifecycle_state_since=as_of_date,
        )
        rows[ticker] = row
        session.add(row)
    if row.lifecycle_state != section:
        row.last_material_event = f"{row.lifecycle_state}_TO_{section}"
        row.last_material_event_date = as_of_date
        row.lifecycle_state_since = as_of_date
    row.lifecycle_state = section
    # first_screen_date restarts when an exited name re-enters the screen.
    row.first_discovered_date = date.fromisoformat(state["first_screen_date"])
    row.last_discovery_screen_date = date.fromisoformat(state["last_screen_date"]) if state.get("last_screen_date") else None
    row.days_on_watch = max(0, (as_of_date - row.first_discovered_date).days)
    row.active_trigger = Decimal(state["anchor_trigger"]) if state.get("anchor_trigger") else None
    row.active_trigger_set_date = date.fromisoformat(state["anchor_date"]) if state.get("anchor_date") else None
    if candidate is not None:
        row.rolling_trigger = Decimal(candidate["trigger_price"]) if candidate.get("trigger_price") else None
        row.current_trigger_distance_pct = Decimal(candidate["distance_to_trigger_pct"]) if candidate.get("distance_to_trigger_pct") else None
        if row.current_trigger_distance_pct is not None and (
            row.best_trigger_distance_pct is None or row.current_trigger_distance_pct < row.best_trigger_distance_pct
        ):
            row.best_trigger_distance_pct = row.current_trigger_distance_pct
        row.previous_relative_strength_20d = row.relative_strength_20d
        rs = candidate["metrics"].get("relative_return_20d_vs_qqq_pct")
        row.relative_strength_20d = Decimal(str(rs)) if rs is not None else None
    if state.get("exited"):
        row.archive_reason = (candidate or {}).get("payload", {}).get("exit_reason") or "no longer tracked"
        row.archived_date = as_of_date
    else:
        row.archive_reason = None
        row.archived_date = None
    row.metadata_json = {"v09_state": state}


def bootstrap_daily_stock_alert_shadow(
    session: Session,
    *,
    start_date: date,
    end_date: date,
    record_from: date | None = None,
) -> dict[str, Any]:
    """Replay v0.9 tracking point-in-time over stored sessions; resumable, never rewrites history."""
    session_dates = [day for day in _session_dates(session, end_date, (end_date - start_date).days + 1) if day >= start_date]
    processed = {
        (row.metadata_json or {}).get("v09_state", {}).get("last_processed_date")
        for row in session.scalars(
            select(StrategyCandidateLifecycle).where(StrategyCandidateLifecycle.strategy_key == LIFECYCLE_KEY)
        ).all()
    } - {None}
    resume_after = date.fromisoformat(max(processed)) if processed else None
    results = []
    for market_date in session_dates:
        if resume_after is not None and market_date <= resume_after:
            continue
        result = run_daily_stock_alert_shadow(
            session, as_of_date=market_date,
            record_run=record_from is not None and market_date >= record_from, replayed=True,
        )
        results.append({"as_of_date": result["as_of_date"], "status": result["status"],
                        "run_id": result.get("run_id")})
    return {"lifecycle_key": LIFECYCLE_KEY, "sessions": len(results),
            "recorded_runs": sum(1 for item in results if item["status"] == "recorded"),
            "already_processed": sum(1 for item in results if item["status"] == "already_processed"),
            "results": results}
