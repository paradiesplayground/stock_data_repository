"""Forward outcomes for daily-alert candidates (the live scorecard).

Every candidate in an as-run or shadow daily-alert run gets server-owned outcome
observations once enough completed sessions exist:

* ``fwd_5s`` / ``fwd_10s`` / ``fwd_20s``: close-to-close return from the alert
  close, the same-window QQQ return, and the high/low excursion.
* ``trade_path``: what the alert's own trade plan would have done. Entry on the
  first session whose high reaches the trigger (filled at the open when it gaps
  above), stop at the alert's invalidation price, and a 2R target. Same-session
  stop and target count as a stop. The record stays open until it resolves.

Observations are keyed to the alert date. Final records are never rewritten.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import (
    DailyPriceBar,
    StrategyCandidate,
    StrategyDefinition,
    StrategyOutcomeObservation,
    StrategyRun,
)
from app.strategy_decisions import StrategyCandidateDecision

STRATEGY_KEY = "dynamic_swing_buy_alerts"
RUN_TYPES = ("as_run", "shadow")
FORWARD_HORIZONS = (5, 10, 20)
TRIGGER_WINDOW_SESSIONS = 20
HOLD_SESSIONS = 20
TARGET_R = Decimal("2")
MODEL = "alert-outcome-v1"
BENCHMARK = "QQQ"
FINAL_TRADE_STATUSES = {"target_2r", "stopped", "expired", "not_triggered", "no_trade_plan"}
RESOLVED_TRADE_STATUSES = {"target_2r", "stopped", "expired"}
DEFAULT_LOOKBACK_DAYS = 90
EXECUTION_ASSUMPTIONS = {
    "model": MODEL,
    "entry": "first session high >= trigger within 20 sessions; fill at max(trigger, open)",
    "stop": "alert invalidation price; a gap below fills at the open",
    "target": "2R from the fill",
    "same_session_stop_and_target": "stop first",
    "hold_sessions": HOLD_SESSIONS,
    "benchmark": BENCHMARK,
}


@dataclass(frozen=True)
class Bar:
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


def _pct(current: Decimal, base: Decimal) -> Decimal:
    return (current / base - Decimal("1")) * Decimal("100")


def _text(value: Decimal | None, places: str = "0.0001") -> str | None:
    return None if value is None else str(value.quantize(Decimal(places)))


def forward_window(
    bars: list[Bar],
    alert_close: Decimal,
    sessions: int,
    benchmark_bars: list[Bar] | None = None,
    benchmark_alert_close: Decimal | None = None,
) -> dict[str, Any] | None:
    """Close-to-close result after exactly ``sessions`` completed sessions."""
    if alert_close <= 0 or len(bars) < sessions:
        return None
    window = bars[:sessions]
    metrics: dict[str, Any] = {
        "sessions": sessions,
        "end_date": window[-1].trade_date.isoformat(),
        "close_return_pct": _text(_pct(window[-1].close, alert_close)),
        "max_high_pct": _text(_pct(max(bar.high for bar in window), alert_close)),
        "min_low_pct": _text(_pct(min(bar.low for bar in window), alert_close)),
        "benchmark": BENCHMARK,
        "benchmark_return_pct": None,
        "excess_return_pct": None,
    }
    benchmark_end = {bar.trade_date: bar.close for bar in benchmark_bars or []}.get(
        window[-1].trade_date
    )
    if benchmark_end is not None and benchmark_alert_close:
        benchmark_return = _pct(benchmark_end, benchmark_alert_close)
        metrics["benchmark_return_pct"] = _text(benchmark_return)
        metrics["excess_return_pct"] = _text(
            _pct(window[-1].close, alert_close) - benchmark_return
        )
    return metrics


def trade_path(
    bars: list[Bar], trigger: Decimal | None, stop: Decimal | None
) -> tuple[str, dict[str, Any]]:
    """Follow the alert's own trade plan through the completed sessions."""
    base: dict[str, Any] = {
        "trigger": _text(trigger),
        "stop": _text(stop),
        "sessions_observed": len(bars),
    }
    if trigger is None or stop is None or stop >= trigger:
        return "no_trade_plan", base
    entry_index = next(
        (index for index, bar in enumerate(bars[:TRIGGER_WINDOW_SESSIONS]) if bar.high >= trigger),
        None,
    )
    if entry_index is None:
        status = "not_triggered" if len(bars) >= TRIGGER_WINDOW_SESSIONS else "pending"
        return status, base
    entry_bar = bars[entry_index]
    entry = max(trigger, entry_bar.open)
    risk = entry - stop
    target = entry + TARGET_R * risk
    one_r = entry + risk
    metrics = {
        **base,
        "entry_date": entry_bar.trade_date.isoformat(),
        "entry_price": _text(entry),
        "sessions_to_entry": entry_index + 1,
        "risk_per_share": _text(risk),
        "risk_pct": _text(risk / entry * Decimal("100")),
        "first_1r_date": None,
        "first_2r_date": None,
        "exit_date": None,
        "exit_price": None,
        "exit_r": None,
        "max_favorable_r": None,
        "max_adverse_r": None,
        "sessions_held": 0,
    }
    held = bars[entry_index : entry_index + HOLD_SESSIONS]
    best = worst = Decimal("0")
    status = "open"
    for position, bar in enumerate(held):
        metrics["sessions_held"] = position + 1
        if bar.low <= stop:
            # The entry session can only stop after the fill; later sessions
            # that open below the stop fill at the open.
            exit_price = bar.open if position > 0 and bar.open < stop else stop
            worst = min(worst, (exit_price - entry) / risk)
            metrics.update(exit_date=bar.trade_date.isoformat(), exit_price=_text(exit_price),
                           exit_r=_text((exit_price - entry) / risk))
            status = "stopped"
            break
        best = max(best, (bar.high - entry) / risk)
        worst = min(worst, (bar.low - entry) / risk)
        if metrics["first_1r_date"] is None and bar.high >= one_r:
            metrics["first_1r_date"] = bar.trade_date.isoformat()
        if bar.high >= target:
            metrics.update(first_2r_date=bar.trade_date.isoformat(), exit_date=bar.trade_date.isoformat(),
                           exit_price=_text(target), exit_r=_text(TARGET_R))
            status = "target_2r"
            break
    if status == "open" and len(held) >= HOLD_SESSIONS:
        last = held[-1]
        metrics.update(exit_date=last.trade_date.isoformat(), exit_price=_text(last.close),
                       exit_r=_text((last.close - entry) / risk))
        status = "expired"
    metrics["max_favorable_r"] = _text(best)
    metrics["max_adverse_r"] = _text(worst)
    return status, metrics


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - malformed payload values are treated as missing
        return None


def _runs(
    session: Session,
    *,
    strategy_key: str,
    run_types: Iterable[str],
    since: date | None,
    until: date | None = None,
    strategy_version: str | None = None,
) -> list[tuple[StrategyRun, StrategyDefinition]]:
    statement = (
        select(StrategyRun, StrategyDefinition)
        .join(StrategyDefinition, StrategyDefinition.id == StrategyRun.strategy_definition_id)
        .where(
            StrategyDefinition.strategy_key == strategy_key,
            StrategyRun.run_type.in_(tuple(run_types)),
        )
        .order_by(StrategyRun.as_of_date, desc(StrategyRun.generated_at_utc))
    )
    if since is not None:
        statement = statement.where(StrategyRun.as_of_date >= since)
    if until is not None:
        statement = statement.where(StrategyRun.as_of_date <= until)
    if strategy_version is not None:
        statement = statement.where(StrategyDefinition.version == strategy_version)
    return list(session.execute(statement).all())


def _latest_runs_per_day(
    rows: list[tuple[StrategyRun, StrategyDefinition]],
) -> list[tuple[StrategyRun, StrategyDefinition]]:
    """Same-date revisions supersede earlier ones; keep the latest per version/type/date."""
    latest: dict[tuple[str, str, date], tuple[StrategyRun, StrategyDefinition]] = {}
    for run, definition in rows:
        key = (definition.version, run.run_type, run.as_of_date)
        current = latest.get(key)
        if current is None or run.generated_at_utc > current[0].generated_at_utc:
            latest[key] = (run, definition)
    return sorted(latest.values(), key=lambda item: (item[0].as_of_date, item[1].version))


def _bars_after(
    session: Session, tickers: set[str], as_of_date: date, limit: int
) -> dict[str, list[Bar]]:
    rows = session.scalars(
        select(DailyPriceBar)
        .where(
            DailyPriceBar.ticker.in_(tickers),
            DailyPriceBar.trade_date > as_of_date,
            DailyPriceBar.trade_date <= as_of_date + timedelta(days=limit * 2 + 10),
        )
        .order_by(DailyPriceBar.ticker, DailyPriceBar.trade_date)
    ).all()
    bars: dict[str, list[Bar]] = defaultdict(list)
    for row in rows:
        if len(bars[row.ticker]) < limit:
            bars[row.ticker].append(
                Bar(row.trade_date, row.open, row.high, row.low, row.close)
            )
    return bars


def _close_on(session: Session, ticker: str, as_of_date: date) -> Decimal | None:
    return session.scalar(
        select(DailyPriceBar.close).where(
            DailyPriceBar.ticker == ticker, DailyPriceBar.trade_date == as_of_date
        )
    )


def sync_alert_outcomes(
    session: Session,
    *,
    strategy_key: str = STRATEGY_KEY,
    since: date | None = None,
    today: date | None = None,
    run_types: Iterable[str] = RUN_TYPES,
) -> dict[str, Any]:
    """Write or advance outcome observations for recent alert runs (idempotent)."""
    since = since or (today or date.today()) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    runs = _latest_runs_per_day(
        _runs(session, strategy_key=strategy_key, run_types=run_types, since=since)
    )
    written = updated = unchanged = runs_seen = 0
    for run, _definition in runs:
        decisions = session.scalars(
            select(StrategyCandidateDecision).where(StrategyCandidateDecision.run_id == run.run_id)
        ).all()
        if not decisions:
            continue
        runs_seen += 1
        payloads = {
            candidate.ticker: candidate.payload or {}
            for candidate in session.scalars(
                select(StrategyCandidate).where(StrategyCandidate.run_id == run.run_id)
            ).all()
        }
        existing = {
            (item.ticker, item.horizon): item
            for item in session.scalars(
                select(StrategyOutcomeObservation).where(
                    StrategyOutcomeObservation.run_id == run.run_id,
                    StrategyOutcomeObservation.observation_date == run.as_of_date,
                )
            ).all()
        }
        tickers = {decision.ticker for decision in decisions}
        session_limit = TRIGGER_WINDOW_SESSIONS + HOLD_SESSIONS
        bars = _bars_after(session, tickers | {BENCHMARK}, run.as_of_date, session_limit)
        benchmark_close = _close_on(session, BENCHMARK, run.as_of_date)
        for decision in decisions:
            payload = payloads.get(decision.ticker, {})
            context = {
                "setup_status": str(payload.get("setup_buyability_status") or decision.buyability_status or ""),
                "effective_status": decision.buyability_status,
                "market_regime_gate_passed": decision.market_regime_gate_passed,
                "distance_to_trigger_pct": _text(_decimal(decision.distance_to_trigger_pct)),
            }
            ticker_bars = bars.get(decision.ticker, [])
            alert_close = _decimal(decision.current_price) or _close_on(session, decision.ticker, run.as_of_date)
            observations: list[tuple[str, str, dict[str, Any]]] = []
            if alert_close:
                for sessions in FORWARD_HORIZONS:
                    window = forward_window(ticker_bars, alert_close, sessions, bars.get(BENCHMARK), benchmark_close)
                    if window is not None:
                        observations.append((f"fwd_{sessions}s", "observed", {**context, **window}))
            status, path = trade_path(ticker_bars, _decimal(decision.trigger_price), _decimal(decision.invalidation_price))
            observations.append(("trade_path", status, {**context, **path}))
            for horizon, status, metrics in observations:
                current = existing.get((decision.ticker, horizon))
                if current is None:
                    session.add(StrategyOutcomeObservation(
                        run_id=run.run_id, ticker=decision.ticker, observation_date=run.as_of_date,
                        horizon=horizon, status=status, metrics=metrics,
                        execution_assumptions=EXECUTION_ASSUMPTIONS,
                    ))
                    written += 1
                elif horizon == "trade_path" and current.status not in FINAL_TRADE_STATUSES and (
                    current.status != status or current.metrics != metrics
                ):
                    current.status, current.metrics = status, metrics
                    updated += 1
                else:
                    unchanged += 1
        session.commit()
    return {"runs": runs_seen, "written": written, "updated": updated, "unchanged": unchanged,
            "since": since.isoformat(), "model": MODEL}


def _mean(values: list[Decimal]) -> str | None:
    return _text(sum(values, Decimal("0")) / len(values)) if values else None


def alert_outcome_scorecard(
    session: Session,
    *,
    strategy_key: str = STRATEGY_KEY,
    strategy_version: str | None = None,
    run_type: str = "as_run",
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Summarize recorded outcomes by setup status. Trades are deduped by ticker and fill date."""
    runs = _latest_runs_per_day(
        _runs(session, strategy_key=strategy_key, run_types=(run_type,), since=start_date,
              until=end_date, strategy_version=strategy_version)
    )
    run_ids = [run.run_id for run, _ in runs]
    observations = session.scalars(
        select(StrategyOutcomeObservation).where(StrategyOutcomeObservation.run_id.in_(run_ids))
    ).all() if run_ids else []
    run_dates = {run.run_id: run.as_of_date for run, _ in runs}
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "candidate_days": 0, "forward": defaultdict(list), "trades": {},
    })
    for item in sorted(observations, key=lambda obs: run_dates.get(obs.run_id, date.min)):
        metrics = item.metrics or {}
        group = groups[metrics.get("setup_status") or "UNKNOWN"]
        if item.horizon == "trade_path":
            group["candidate_days"] += 1
            if metrics.get("entry_date"):
                group["trades"].setdefault((item.ticker, metrics["entry_date"]), (item.status, metrics))
        elif item.horizon.startswith("fwd_"):
            group["forward"][item.horizon].append(metrics)
    summary: dict[str, Any] = {}
    for setup_status, group in sorted(groups.items()):
        forward = {}
        for horizon in (f"fwd_{sessions}s" for sessions in FORWARD_HORIZONS):
            rows = group["forward"].get(horizon, [])
            returns = [_decimal(row.get("close_return_pct")) for row in rows]
            excess = [_decimal(row.get("excess_return_pct")) for row in rows]
            forward[horizon] = {
                "observations": len(rows),
                "mean_return_pct": _mean([value for value in returns if value is not None]),
                "mean_excess_vs_qqq_pct": _mean([value for value in excess if value is not None]),
                "beat_qqq_pct": _text(
                    Decimal(sum(1 for value in excess if value is not None and value > 0) * 100)
                    / len([value for value in excess if value is not None]), "0.1"
                ) if any(value is not None for value in excess) else None,
            }
        trades = list(group["trades"].values())
        resolved = [(status, metrics) for status, metrics in trades if status in RESOLVED_TRADE_STATUSES]
        exit_r = [_decimal(metrics.get("exit_r")) for _, metrics in resolved]
        summary[setup_status] = {
            "candidate_days": group["candidate_days"],
            "forward": forward,
            "trades": {
                "unique_fills": len(trades),
                "resolved": len(resolved),
                "open": sum(1 for status, _ in trades if status == "open"),
                "target_2r": sum(1 for status, _ in resolved if status == "target_2r"),
                "stopped": sum(1 for status, _ in resolved if status == "stopped"),
                "expired": sum(1 for status, _ in resolved if status == "expired"),
                "reached_1r": sum(1 for _, metrics in trades if metrics.get("first_1r_date")),
                "expectancy_r": _mean([value for value in exit_r if value is not None]),
            },
        }
    return {
        "strategy_key": strategy_key,
        "strategy_version": strategy_version,
        "run_type": run_type,
        "runs": len(runs),
        "start_date": runs[0][0].as_of_date.isoformat() if runs else None,
        "end_date": runs[-1][0].as_of_date.isoformat() if runs else None,
        "model": MODEL,
        "execution_assumptions": EXECUTION_ASSUMPTIONS,
        "by_setup_status": summary,
        "interpretation": (
            "Repeated daily alerts of one name are correlated; judge trades by unique fills, "
            "and treat small samples as directional only."
        ),
    }
