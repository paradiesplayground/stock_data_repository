"""Deterministic v0.9 daily-alert engine (pure; no database access).

One call evaluates one ticker for one completed session from its derived
features (calculation 1.6.0+), its tracking state, the market gate, and its
latest saved research. It returns the canonical v0.9 candidate and the next
tracking state. The server assigns every label; research can only veto through
typed blocker codes.

Sections (email wording / contract status):

* Buy today / BUY_NOW: closed 0-5% above the trigger within N sessions of first
  crossing it, positive 20-day RS vs QQQ, market gate passing, current research,
  not blocked or broken.
* Close / ALMOST_READY: 0-5% below the trigger with every other gate passing.
* Recovering / RADAR: every other tracked name that is not blocked.
* Blocked / NOT_ELIGIBLE (bucket ``blocked``): a deterministic risk flag or a
  research blocker; re-checked every session, so it clears on its own.
* Exited / NOT_ELIGIBLE (bucket ``exited``): left tracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "alerts" / "dynamic-swing-buy-alerts-v0.9.json"
)
HUNDRED = Decimal("100")

SECTION_BUY = "buy"
SECTION_CLOSE = "close"
SECTION_RECOVERING = "recovering"
SECTION_BLOCKED = "blocked"
SECTION_EXITED = "exited"
SECTION_INCOMPLETE = "incomplete"


def load_configuration(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class EngineConfig:
    min_price: Decimal
    min_market_cap: Decimal
    min_ttm_growth: Decimal
    min_quarter_growth: Decimal
    max_change_12w: Decimal
    min_dollar_volume: Decimal
    trigger_above_high: Decimal
    stop_atr_multiple: Decimal
    anchor_within: Decimal
    break_below_trigger: Decimal
    close_band: Decimal
    buy_band: Decimal
    buy_max_sessions_above: int
    research_max_age_days: int
    max_sessions_outside_screen: int
    no_progress_sessions: int
    progress_within: Decimal
    max_share_count_growth: Decimal
    min_runway_months: Decimal
    unknown_sic_blocks: bool

    @classmethod
    def from_configuration(cls, configuration: dict[str, Any]) -> "EngineConfig":
        universe, hard = configuration["universe"], configuration["hard_thresholds"]
        trigger, labels = configuration["trigger_model"], configuration["labels"]
        tracking, blocking = configuration["tracking"], configuration["blocking"]

        def number(value: Any) -> Decimal:
            return Decimal(str(value))

        return cls(
            min_price=number(universe["minimum_price"]),
            min_market_cap=number(universe["minimum_market_cap"]),
            min_ttm_growth=number(hard["minimum_ttm_revenue_growth_pct"]),
            min_quarter_growth=number(hard["minimum_quarter_revenue_growth_pct"]),
            max_change_12w=number(hard["maximum_price_change_12w_pct"]),
            min_dollar_volume=number(hard["minimum_avg_dollar_volume_20d"]),
            trigger_above_high=number(trigger["above_high_pct"]),
            stop_atr_multiple=number(trigger["stop_atr_multiple"]),
            anchor_within=number(trigger["anchor_within_pct"]),
            break_below_trigger=number(trigger["break_below_trigger_pct"]),
            close_band=number(labels["close_band_pct"]),
            buy_band=number(labels["buy_band_pct"]),
            buy_max_sessions_above=int(labels["buy_max_sessions_above"]),
            research_max_age_days=int(labels["research_max_age_days"]),
            max_sessions_outside_screen=int(tracking["max_sessions_outside_screen"]),
            no_progress_sessions=int(tracking["no_progress_sessions"]),
            progress_within=number(tracking["progress_within_pct"]),
            max_share_count_growth=number(blocking["max_share_count_yoy_pct"]),
            min_runway_months=number(blocking["min_cash_runway_months"]),
            unknown_sic_blocks=bool(blocking["unknown_sic_blocks"]),
        )


@dataclass(frozen=True)
class Research:
    as_of_date: date
    blocker_codes: tuple[str, ...] = ()
    # Typed findings are recorded for measurement only; they never change a label.
    fields: dict[str, Any] | None = None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _date(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _money(value: Decimal | None) -> str:
    return f"${value:,.2f}" if value is not None else "n/a"


def passes_base_gates(item: dict[str, Any], config: EngineConfig, listing_ok: bool) -> bool:
    """Growth, liquidity, size, and listing gates; a failure ends tracking."""
    checks = (
        (_decimal(item.get("close")), config.min_price),
        (_decimal(item.get("approximate_market_cap")), config.min_market_cap),
        (_decimal(item.get("revenue_ttm_yoy_pct")), config.min_ttm_growth),
        (_decimal(item.get("latest_quarter_revenue_yoy_pct")), config.min_quarter_growth),
        (_decimal(item.get("avg_dollar_volume_20d")), config.min_dollar_volume),
    )
    return listing_ok and all(value is not None and value >= minimum for value, minimum in checks)


def in_screen(item: dict[str, Any], config: EngineConfig, listing_ok: bool) -> bool:
    change = _decimal(item.get("price_change_12w_pct"))
    return (
        passes_base_gates(item, config, listing_ok)
        and change is not None
        and change <= config.max_change_12w
    )


def deterministic_risk_flags(item: dict[str, Any], config: EngineConfig) -> list[str]:
    flags: list[str] = []
    if config.unknown_sic_blocks and not item.get("sic_code"):
        flags.append("unknown_sic")
    runway = _decimal(item.get("cash_runway_months"))
    free_cash_flow = _decimal(item.get("free_cash_flow_ttm"))
    if runway is None and (free_cash_flow is None or free_cash_flow < 0):
        flags.append("cash_runway_unavailable")
    elif runway is not None and runway < config.min_runway_months:
        flags.append("cash_runway_below_minimum")
    dilution = _decimal(item.get("share_count_yoy_pct"))
    if dilution is not None and dilution >= config.max_share_count_growth:
        flags.append("share_count_growth_at_or_above_limit")
    return flags


def new_state(as_of_date: date) -> dict[str, Any]:
    return {
        "first_screen_date": as_of_date.isoformat(),
        "last_screen_date": as_of_date.isoformat(),
        "anchor_trigger": None,
        "anchor_stop": None,
        "anchor_date": None,
        "above_since": None,
        "last_near_date": None,
        "last_processed_date": None,
        "section": None,
        "exited": False,
    }


def evaluate(
    item: dict[str, Any],
    state: dict[str, Any] | None,
    *,
    as_of_date: date,
    listing_ok: bool,
    market_gate_passed: bool,
    research: Research | None,
    sessions_between: Callable[[date, date], int],
    config: EngineConfig,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (candidate, next_state). ``(None, None)`` means not tracked and not in the screen."""
    ticker = str(item["ticker"]).upper()
    screened = in_screen(item, config, listing_ok)
    if state is None or state.get("exited"):
        if not screened:
            return None, None
        state = new_state(as_of_date)
    state = dict(state)
    if screened:
        state["last_screen_date"] = as_of_date.isoformat()
    last_screen = _date(state["last_screen_date"]) or as_of_date
    sessions_out = 0 if screened else sessions_between(last_screen, as_of_date)

    close = _decimal(item.get("close"))
    prior_high = _decimal(item.get("prior_high_20d"))
    prior_low = _decimal(item.get("prior_low_20d"))
    prior_atr = _decimal(item.get("prior_atr_14"))
    rs = _decimal(item.get("relative_return_20d_vs_qqq_pct"))
    state["last_processed_date"] = as_of_date.isoformat()

    payload: dict[str, Any] = {
        "company_name": item.get("company"),
        "in_screen": screened,
        "sessions_outside_screen": sessions_out,
        "tracking_since": state["first_screen_date"],
    }

    def finish(
        section: str,
        *,
        bucket: str,
        status: str,
        technical_state: str,
        reason: str,
        conditions: list[str],
        remaining: int,
        trigger: Decimal | None = None,
        stop: Decimal | None = None,
        codes: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        state["section"] = section
        distance = ((trigger - close) / trigger) * HUNDRED if trigger is not None and close else None
        candidate = {
            "ticker": ticker,
            "screen_bucket": bucket,
            "technical_state": technical_state,
            "buyability_status": status,
            "status_reason": reason,
            "buy_conditions": conditions,
            "remaining_gate_count": remaining,
            "current_price": str(close),
            "trigger_price": str(trigger) if trigger is not None else None,
            "distance_to_trigger_pct": str(distance) if distance is not None else None,
            "invalidation_price": str(stop) if stop is not None else None,
            "technical_gate_passed": bool(trigger is not None and close >= trigger),
            "market_regime_gate_passed": market_gate_passed,
            "blocker_codes": codes or [],
            "metrics": item,
            "payload": {**payload, "section": section, "blocker_codes": codes or []},
        }
        return candidate, state

    if close is None or close <= 0:
        state.update(exited=True, section=SECTION_EXITED)
        return None, state
    if not passes_base_gates(item, config, listing_ok):
        state["exited"] = True
        payload["exit_reason"] = "failed growth, liquidity, size, or listing gates"
        return finish(SECTION_EXITED, bucket="exited", status="NOT_ELIGIBLE", technical_state="unknown",
                      reason="Stopped tracking: it no longer passes the growth, liquidity, size, or listing gates.",
                      conditions=["Re-enters if it passes the full screen again."], remaining=1)
    if sessions_out > config.max_sessions_outside_screen:
        state["exited"] = True
        payload["exit_reason"] = f"more than {config.max_sessions_outside_screen} sessions outside the screen"
        return finish(SECTION_EXITED, bucket="exited", status="NOT_ELIGIBLE", technical_state="unknown",
                      reason=f"Stopped tracking: {sessions_out} sessions since it last passed the screen.",
                      conditions=["Re-enters if it passes the full screen again."], remaining=1)
    if prior_high is None or prior_low is None or prior_atr is None:
        payload["missing_fields"] = [
            field for field, value in (("prior_high_20d", prior_high), ("prior_low_20d", prior_low), ("prior_atr_14", prior_atr))
            if value is None
        ]
        return finish(SECTION_INCOMPLETE, bucket="incomplete", status="NOT_ELIGIBLE", technical_state="unknown",
                      reason="Prior-session levels are unavailable, so no trigger can be set.",
                      conditions=["Wait for enough price history to set a trigger."], remaining=1)

    rolling = prior_high * (1 + config.trigger_above_high / HUNDRED)
    rolling_stop = max(prior_low, rolling - config.stop_atr_multiple * prior_atr)

    broken = False
    anchor = _decimal(state.get("anchor_trigger"))
    anchor_stop = _decimal(state.get("anchor_stop"))
    if anchor is not None:
        anchor_distance = (anchor - close) / anchor * HUNDRED
        if (anchor_stop is not None and close < anchor_stop) or anchor_distance > config.break_below_trigger:
            broken = True
            state.update(anchor_trigger=None, anchor_stop=None, anchor_date=None, above_since=None)
            anchor = anchor_stop = None
    rolling_distance = (rolling - close) / rolling * HUNDRED
    if anchor is None and not broken and close > rolling_stop and Decimal("0") <= rolling_distance <= config.anchor_within:
        anchor, anchor_stop = rolling, rolling_stop
        state.update(anchor_trigger=str(anchor), anchor_stop=str(anchor_stop), anchor_date=as_of_date.isoformat())
    trigger = anchor if anchor is not None else rolling
    stop = anchor_stop if anchor is not None else rolling_stop
    distance = (trigger - close) / trigger * HUNDRED

    if distance <= config.progress_within:
        state["last_near_date"] = as_of_date.isoformat()
    if distance <= 0:
        state["above_since"] = state.get("above_since") or as_of_date.isoformat()
    else:
        state["above_since"] = None
    above_since = _date(state.get("above_since"))
    sessions_above = (sessions_between(above_since, as_of_date) + 1) if above_since else 0

    if not screened and sessions_out >= config.no_progress_sessions:
        last_near = _date(state.get("last_near_date"))
        if last_near is None or sessions_between(last_near, as_of_date) >= config.no_progress_sessions:
            state["exited"] = True
            payload["exit_reason"] = f"no progress toward the trigger in {config.no_progress_sessions} sessions"
            return finish(SECTION_EXITED, bucket="exited", status="NOT_ELIGIBLE", technical_state="no_setup",
                          reason=f"Stopped tracking: not within {config.progress_within}% of its trigger for {config.no_progress_sessions} sessions.",
                          conditions=["Re-enters if it passes the full screen again."], remaining=1,
                          trigger=trigger, stop=stop)

    bucket = "screen" if screened else "tracked_recovery"
    risk_flags = deterministic_risk_flags(item, config)
    research_current = bool(
        research is not None and 0 <= (as_of_date - research.as_of_date).days <= config.research_max_age_days
    )
    codes = list(research.blocker_codes) if research_current and research else []
    payload.update(
        broken=broken,
        sessions_above_trigger=sessions_above,
        deterministic_risk_flags=risk_flags,
        research_as_of=research.as_of_date.isoformat() if research else None,
        research_current=research_current,
        research_fields=research.fields if research_current and research else None,
    )
    if risk_flags or codes:
        readable = [flag.replace("_", " ") for flag in risk_flags + codes]
        return finish(SECTION_BLOCKED, bucket="blocked", status="NOT_ELIGIBLE",
                      technical_state="broken" if broken else ("near_trigger" if 0 < distance <= config.close_band else "developing"),
                      reason="Blocked: " + "; ".join(readable) + ".",
                      conditions=["Clears automatically when the flag or research blocker no longer applies."],
                      remaining=1 + int(distance > 0), trigger=trigger, stop=stop, codes=risk_flags + codes)

    rs_ok = rs is not None and rs > 0
    failing: list[str] = []
    if not market_gate_passed:
        failing.append("Market gate must pass (SPY above its 50-day average).")
    if not rs_ok:
        failing.append("20-day return must beat QQQ.")
    if not research_current:
        failing.append("Needs current research (queued first when it qualifies).")
    if close <= stop:
        failing.append(f"Price is at or below the {_money(stop)} stop level.")
    payload["needs_research"] = not research_current

    if broken:
        technical_state = "broken"
        failing.insert(0, "Setup broke down; waits for a new base within 10% of a fresh trigger.")
    elif distance <= 0:
        technical_state = "confirmed" if (-distance <= config.buy_band and sessions_above <= config.buy_max_sessions_above) else "extended"
    elif distance <= config.close_band:
        technical_state = "near_trigger"
    elif distance <= config.anchor_within:
        technical_state = "developing"
    else:
        technical_state = "no_setup"

    trigger_text = f"Close above {_money(trigger)} (stop {_money(stop)})."
    if technical_state == "confirmed" and not failing:
        return finish(SECTION_BUY, bucket=bucket, status="BUY_NOW", technical_state="confirmed",
                      reason=f"Closed {-distance:.1f}% above its {_money(trigger)} trigger (day {sessions_above}) with RS {rs:+.1f}% vs QQQ.",
                      conditions=[f"Entry zone up to {_money(trigger * (1 + config.buy_band / HUNDRED))}; stop {_money(stop)}."],
                      remaining=0, trigger=trigger, stop=stop)
    if technical_state == "near_trigger" and not failing:
        return finish(SECTION_CLOSE, bucket=bucket, status="ALMOST_READY", technical_state="near_trigger",
                      reason=f"{distance:.1f}% below its {_money(trigger)} trigger with RS {rs:+.1f}% vs QQQ; only the trigger remains.",
                      conditions=[trigger_text], remaining=1, trigger=trigger, stop=stop)

    if technical_state == "extended":
        failing.insert(0, "Extended past the buy window; wait for a new base.")
    elif technical_state == "near_trigger":
        # Already within the close band: the failing gate is what to watch first.
        failing.append(trigger_text)
    elif technical_state in {"developing", "no_setup"}:
        failing.insert(0, trigger_text)
    position = (
        f"{-distance:.1f}% above" if distance <= 0 else f"{distance:.1f}% below"
    ) + f" its {_money(trigger)} trigger"
    return finish(SECTION_RECOVERING, bucket=bucket, status="RADAR", technical_state=technical_state,
                  reason=f"Recovering: {position}" + ("; setup broke down." if broken else "."),
                  conditions=failing, remaining=max(1, len(failing)), trigger=trigger, stop=stop)
