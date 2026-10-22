"""Weekly side-by-side of the live v0.8 alert and the v0.9 shadow alert.

Built from stored runs and the outcome scorecard only, so the email shows
what each version actually said on each day and what has happened since.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import StrategyCandidate, StrategyDefinition, StrategyOutcomeObservation, StrategyRun
from app.services.alert_outcomes import alert_outcome_scorecard
from app.services.daily_stock_alert_shadow import LIVE_STRATEGY_KEY, SHADOW_STRATEGY_KEY
from app.strategy_decisions import StrategyCandidateDecision

SHADOW_START = date(2026, 9, 30)
ACTIONABLE = ("BUY_NOW", "ALMOST_READY")


def _latest_runs(session: Session, strategy_key: str, run_type: str, start: date, end: date) -> dict[date, StrategyRun]:
    rows = session.execute(
        select(StrategyRun)
        .join(StrategyDefinition, StrategyDefinition.id == StrategyRun.strategy_definition_id)
        .where(
            StrategyDefinition.strategy_key == strategy_key,
            StrategyRun.run_type == run_type,
            StrategyRun.as_of_date.between(start, end),
        )
        .order_by(StrategyRun.as_of_date, desc(StrategyRun.generated_at_utc))
    ).scalars().all()
    latest: dict[date, StrategyRun] = {}
    for run in rows:
        latest.setdefault(run.as_of_date, run)
    return latest


def _statuses(session: Session, run: StrategyRun) -> dict[str, list[str]]:
    payloads = {
        item.ticker: item.payload or {}
        for item in session.scalars(select(StrategyCandidate).where(StrategyCandidate.run_id == run.run_id)).all()
    }
    grouped: dict[str, list[str]] = defaultdict(list)
    for decision in session.scalars(
        select(StrategyCandidateDecision).where(StrategyCandidateDecision.run_id == run.run_id)
    ).all():
        payload = payloads.get(decision.ticker, {})
        status = str(payload.get("setup_buyability_status") or decision.buyability_status or "")
        if payload.get("section") == "blocked":
            status = "BLOCKED"
        grouped[status].append(decision.ticker)
    return {status: sorted(tickers) for status, tickers in grouped.items()}


def _call_outcomes(session: Session, runs: dict[date, StrategyRun]) -> list[dict[str, Any]]:
    """First Buy/Close call per ticker in the week, with what has happened since."""
    calls: dict[tuple[str, str], dict[str, Any]] = {}
    for as_of_date, run in sorted(runs.items()):
        for status, tickers in _statuses(session, run).items():
            if status not in ACTIONABLE:
                continue
            for ticker in tickers:
                calls.setdefault((ticker, status), {"ticker": ticker, "status": status,
                                                    "date": as_of_date.isoformat(), "run_id": run.run_id})
    for call in calls.values():
        observations = {
            item.horizon: item for item in session.scalars(
                select(StrategyOutcomeObservation).where(
                    StrategyOutcomeObservation.run_id == call.pop("run_id"),
                    StrategyOutcomeObservation.ticker == call["ticker"],
                )
            ).all()
        }
        path = observations.get("trade_path")
        call["trade"] = path.status if path else "pending"
        call["exit_r"] = (path.metrics or {}).get("exit_r") if path else None
        for horizon in ("fwd_5s", "fwd_10s"):
            if horizon in observations:
                call[horizon] = (observations[horizon].metrics or {}).get("close_return_pct")
    return sorted(calls.values(), key=lambda item: (item["date"], item["ticker"]))


def _scorecard_rows(card: dict[str, Any]) -> dict[str, Any]:
    rows = {}
    for status, group in (card.get("by_setup_status") or {}).items():
        trades = group.get("trades") or {}
        forward = (group.get("forward") or {}).get("fwd_10s") or {}
        rows[status] = {
            "candidate_days": group.get("candidate_days"),
            "fwd_10s_excess_vs_qqq_pct": forward.get("mean_excess_vs_qqq_pct"),
            "fwd_10s_observations": forward.get("observations"),
            "unique_fills": trades.get("unique_fills"),
            "target_2r": trades.get("target_2r"),
            "stopped": trades.get("stopped"),
            "open": trades.get("open"),
            "expectancy_r": trades.get("expectancy_r"),
        }
    return rows


def build_weekly_comparison(session: Session, week_ending: date) -> dict[str, Any]:
    start = week_ending - timedelta(days=6)
    live = _latest_runs(session, LIVE_STRATEGY_KEY, "as_run", start, week_ending)
    shadow = _latest_runs(session, SHADOW_STRATEGY_KEY, "shadow", start, week_ending)
    days = []
    for as_of_date in sorted(set(live) | set(shadow)):
        v08 = _statuses(session, live[as_of_date]) if as_of_date in live else None
        v09_run = shadow.get(as_of_date)
        v09 = _statuses(session, v09_run) if v09_run else None
        regime = ((v09_run.summary or {}).get("market_regime") or {}).get("status") if v09_run else None
        days.append({
            "date": as_of_date.isoformat(),
            "market_gate": regime,
            "v08": None if v08 is None else {
                "buy": v08.get("BUY_NOW", []), "almost_ready": v08.get("ALMOST_READY", []),
                "radar": v08.get("RADAR", []), "not_eligible_count": len(v08.get("NOT_ELIGIBLE", [])),
            },
            "v09": None if v09 is None else {
                "buy": v09.get("BUY_NOW", []), "close": v09.get("ALMOST_READY", []),
                "recovering_count": len(v09.get("RADAR", [])), "blocked_count": len(v09.get("BLOCKED", [])),
                "run_id": v09_run.run_id,
            },
        })
    v09_calls = _call_outcomes(session, shadow)
    v08_calls = _call_outcomes(session, live)
    scorecard = {
        "since": SHADOW_START.isoformat(),
        "v08": _scorecard_rows(alert_outcome_scorecard(
            session, strategy_key=LIVE_STRATEGY_KEY, run_type="as_run", start_date=SHADOW_START, end_date=week_ending)),
        "v09": _scorecard_rows(alert_outcome_scorecard(
            session, strategy_key=SHADOW_STRATEGY_KEY, run_type="shadow", start_date=SHADOW_START, end_date=week_ending)),
    }
    v09_buy_days = sum(1 for day in days if day["v09"] and (day["v09"]["buy"] or day["v09"]["close"]))
    v08_buy_days = sum(1 for day in days if day["v08"] and (day["v08"]["buy"] or day["v08"]["almost_ready"]))
    headline = (
        f"v0.9 had buy or close calls on {v09_buy_days} of {len(days)} days; "
        f"v0.8 on {v08_buy_days}."
    )
    comparison = {
        "week_key": f"{week_ending.isocalendar().year}-W{week_ending.isocalendar().week:02d}",
        "week_ending": week_ending.isoformat(),
        "week_start": start.isoformat(),
        "headline": headline,
        "days": days,
        "v09_calls": v09_calls,
        "v08_calls": v08_calls,
        "scorecard": scorecard,
        "notes": [
            "Shadow results are recorded beside the live alert and were not sent as advice.",
            "Forward returns are close-to-close from the alert date; trades follow each call's own trigger and stop.",
            "Small samples: treat as directional until the scorecard has at least 30 resolved trades.",
        ],
    }
    comparison["markdown"] = render_comparison_markdown(comparison)
    return comparison


def _fmt_pct(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):+.1f}%"
    except Exception:  # noqa: BLE001
        return "n/a"


def render_comparison_markdown(comparison: dict[str, Any]) -> str:
    lines = [f"# Weekly alert comparison — {comparison['week_start']} to {comparison['week_ending']}", "",
             f"**{comparison['headline']}**", "", "## Day by day", "",
             "| Date | Market | v0.8 buy / almost ready | v0.8 radar | v0.9 buy | v0.9 close | v0.9 recovering / blocked |",
             "|---|---|---|---|---|---|---|"]
    for day in comparison["days"]:
        v08, v09 = day["v08"], day["v09"]
        lines.append("| " + " | ".join([
            day["date"], day["market_gate"] or "n/a",
            ", ".join((v08 or {}).get("buy", []) + (v08 or {}).get("almost_ready", [])) or ("none" if v08 else "no run"),
            ", ".join((v08 or {}).get("radar", [])) or ("none" if v08 else "no run"),
            ", ".join((v09 or {}).get("buy", [])) or ("none" if v09 else "no run"),
            ", ".join((v09 or {}).get("close", [])) or ("none" if v09 else "no run"),
            f"{v09['recovering_count']} / {v09['blocked_count']}" if v09 else "no run",
        ]) + " |")
    for title, calls in (("v0.9 calls this week", comparison["v09_calls"]), ("v0.8 calls this week", comparison["v08_calls"])):
        lines += ["", f"## {title}"]
        if not calls:
            lines.append("- none")
        for call in calls:
            label = "Buy" if call["status"] == "BUY_NOW" else "Close"
            lines.append(
                f"- {call['date']} {label} **{call['ticker']}**: trade {call['trade'].replace('_', ' ')}"
                + (f" ({call['exit_r']}R)" if call.get("exit_r") else "")
                + (f", 5-day {_fmt_pct(call['fwd_5s'])}" if call.get("fwd_5s") else "")
                + (f", 10-day {_fmt_pct(call['fwd_10s'])}" if call.get("fwd_10s") else "")
            )
    lines += ["", f"## Scorecard since {comparison['scorecard']['since']}", "",
              "| Version | Status | Days | 10-day vs QQQ | Fills | 2R | Stopped | Open | Avg R |",
              "|---|---|---|---|---|---|---|---|---|"]
    for version in ("v09", "v08"):
        for status, row in sorted(comparison["scorecard"][version].items()):
            lines.append("| " + " | ".join(str(value) for value in [
                version.replace("v0", "v0."), status, row["candidate_days"],
                _fmt_pct(row["fwd_10s_excess_vs_qqq_pct"]) if row["fwd_10s_excess_vs_qqq_pct"] else "n/a",
                row["unique_fills"], row["target_2r"], row["stopped"], row["open"], row["expectancy_r"] or "n/a",
            ]) + " |")
    lines += [""] + [f"- {note}" for note in comparison["notes"]]
    return "\n".join(lines)
