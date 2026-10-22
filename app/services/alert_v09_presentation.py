"""Server-owned v0.9 presentation: "what to buy, what is close, and why".

The website and email render this structure as-is; they never re-derive
labels, ordering, caps, or targets. One builder feeds the stored summary,
the compact Markdown report, and both renderers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.services.alert_v09_engine import (
    SECTION_BLOCKED,
    SECTION_BUY,
    SECTION_CLOSE,
    SECTION_EXITED,
    SECTION_RECOVERING,
)
from app.services.daily_stock_alert_report import structural_targets

PRESENTATION_VERSION = "v0.9-presentation-1"
SECTION_TITLES = {
    SECTION_BUY: "Buy today",
    SECTION_CLOSE: "Close",
    SECTION_RECOVERING: "Recovering",
    SECTION_BLOCKED: "Blocked",
    SECTION_EXITED: "Stopped tracking",
}
MAX_CHANGES = 8


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - malformed values are shown as unavailable
        return None


def _money(value: Decimal | None) -> str | None:
    return f"${value:,.2f}" if value is not None else None


def _pct(value: Decimal | None, signed: bool = False) -> str | None:
    if value is None:
        return None
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def _position(candidate: dict[str, Any]) -> str | None:
    distance = _decimal(candidate.get("distance_to_trigger_pct"))
    if distance is None:
        return None
    return f"{-distance:.1f}% above trigger" if distance <= 0 else f"{distance:.1f}% below trigger"


def card(candidate: dict[str, Any], entry_band_pct: Decimal = Decimal("5")) -> dict[str, Any]:
    """One renderer-ready card: plan, first realistic target, why, and what happens next."""
    metrics = candidate.get("metrics") or {}
    payload = candidate.get("payload") or {}
    close = _decimal(candidate.get("current_price"))
    trigger = _decimal(candidate.get("trigger_price"))
    stop = _decimal(candidate.get("invalidation_price"))
    entry = max(trigger, close) if trigger is not None and close is not None and payload.get("section") == SECTION_BUY else trigger
    risk = entry - stop if entry is not None and stop is not None and entry > stop else None
    target = None
    if risk is not None:
        for item in structural_targets(trigger=entry, metrics=metrics):
            price = _decimal(item["price"])
            if price is not None and price > entry:
                target = {"price": _money(price), "basis": item["basis"],
                          "r_multiple": f"{(price - entry) / risk:.1f}R"}
                break
    rs = _decimal(metrics.get("relative_return_20d_vs_qqq_pct"))
    quarter = _decimal(metrics.get("latest_quarter_revenue_yoy_pct"))
    ttm = _decimal(metrics.get("revenue_ttm_yoy_pct"))
    why = [text for text in (
        f"20-day return vs QQQ {_pct(rs, signed=True)}" if rs is not None else None,
        f"Revenue growth {_pct(quarter)} latest quarter, {_pct(ttm)} TTM" if quarter is not None and ttm is not None else None,
        f"{abs(_decimal(metrics.get('price_change_12w_pct')) or 0):.0f}% below its price 12 weeks ago" if (_decimal(metrics.get("price_change_12w_pct")) or 0) < 0 else "Recovered above its 12-week starting price",
    ) if text]
    return {
        "ticker": candidate["ticker"],
        "company": payload.get("company_name"),
        "section": payload.get("section"),
        "status": candidate.get("buyability_status"),
        "headline": candidate.get("status_reason"),
        "current": _money(close),
        "trigger": _money(trigger),
        "entry_zone": (
            f"{_money(trigger)}–{_money(trigger * (1 + entry_band_pct / 100))}"
            if trigger is not None and payload.get("section") == SECTION_BUY else None
        ),
        "stop": _money(stop),
        "risk_pct": _pct(risk / entry * 100) if risk is not None else None,
        "first_target": target,
        "position": _position(candidate),
        "why": why,
        "next": (candidate.get("buy_conditions") or [None])[0],
        "blockers": [code.replace("_", " ") for code in candidate.get("blocker_codes") or payload.get("blocker_codes") or []],
        "unchanged": bool(payload.get("unchanged")),
        "in_screen": bool(payload.get("in_screen")),
        "needs_research": bool(payload.get("needs_research")),
        "exit_reason": payload.get("exit_reason"),
    }


def _changes(candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    priority = {SECTION_BUY: 0, SECTION_CLOSE: 1, SECTION_BLOCKED: 2, SECTION_RECOVERING: 3, SECTION_EXITED: 4}
    changes = []
    for item in candidates:
        payload = item.get("payload") or {}
        section, previous = payload.get("section"), payload.get("previous_section")
        if not payload.get("section_changed") or section == "incomplete":
            continue
        if previous is None:
            text = f"Started tracking: now {SECTION_TITLES.get(section, section)}."
        elif previous == SECTION_BLOCKED:
            text = f"Block cleared: now {SECTION_TITLES.get(section, section)}."
        else:
            text = f"{SECTION_TITLES.get(previous, previous)} → {SECTION_TITLES.get(section, section)}."
        changes.append({"ticker": item["ticker"], "section": section, "text": text})
    changes.sort(key=lambda change: (priority.get(change["section"], 9), change["ticker"]))
    return changes[:MAX_CHANGES]


def build_presentation(
    as_of_date: date,
    candidates: list[dict[str, Any]],
    sections: dict[str, Any],
    *,
    market_ok: bool,
    shadow: bool,
    scorecard_line: str | None = None,
) -> dict[str, Any]:
    by_ticker = {item["ticker"]: item for item in candidates}

    def cards(names: list[str]) -> list[dict[str, Any]]:
        return [card(by_ticker[name]) for name in names if name in by_ticker]

    buy, close, recovering = (sections[name] for name in (SECTION_BUY, SECTION_CLOSE, SECTION_RECOVERING))
    blocked = [item for item in candidates if (item.get("payload") or {}).get("section") == SECTION_BLOCKED]
    buy_total = len(buy["shown"]) + buy["more_on_website"] + len(buy["unchanged"])
    close_total = len(close["shown"]) + close["more_on_website"] + len(close["unchanged"])
    if not market_ok:
        headline = "Market gate closed: no entries today."
    elif buy_total or close_total:
        headline = f"{buy_total} to buy, {close_total} close."
    else:
        headline = "No buy or close setups today."
    return {
        "version": PRESENTATION_VERSION,
        "as_of_date": as_of_date.isoformat(),
        "shadow": shadow,
        "headline": headline,
        "market_gate": "pass" if market_ok else "blocked",
        "sections": {
            SECTION_BUY: {"cards": cards(buy["shown"]), "more": buy["more_on_website"], "unchanged": buy["unchanged"]},
            SECTION_CLOSE: {"cards": cards(close["shown"]), "more": close["more_on_website"], "unchanged": close["unchanged"]},
            SECTION_RECOVERING: {"cards": cards(recovering["shown"]), "more": recovering["more_on_website"],
                                 "unchanged": recovering["unchanged"]},
            SECTION_BLOCKED: {
                "count": len(blocked),
                "changed": [card(item) for item in blocked if (item.get("payload") or {}).get("section_changed")],
            },
        },
        "changes": _changes(candidates),
        "scorecard": scorecard_line,
        "disclaimer": "Watchlist and risk-planning information only. It is not an instruction to place trades.",
    }


def render_markdown(presentation: dict[str, Any]) -> str:
    sections = presentation["sections"]
    title = "Daily Stock Alert v0.9" + (" (SHADOW)" if presentation["shadow"] else "")
    lines = [f"# {title} - {presentation['as_of_date']}", "", f"**{presentation['headline']}**", ""]
    if presentation["shadow"]:
        lines += ["Shadow run: computed beside the live alert, not delivered as advice.", ""]

    def plan(item: dict[str, Any]) -> str:
        parts = [f"Now {item['current']}", f"trigger {item['trigger']}"]
        if item.get("entry_zone"):
            parts.append(f"entry {item['entry_zone']}")
        if item.get("stop"):
            parts.append(f"stop {item['stop']}" + (f" ({item['risk_pct']} risk)" if item.get("risk_pct") else ""))
        if item.get("first_target"):
            target = item["first_target"]
            parts.append(f"target {target['price']} ({target['basis']}, {target['r_multiple']})")
        return " · ".join(parts)

    for key, label in ((SECTION_BUY, "Buy today"), (SECTION_CLOSE, "Close (within 5% of trigger)")):
        detail = sections[key]
        lines.append(f"## {label}")
        if not detail["cards"] and not detail["unchanged"]:
            lines.append("- none")
        for item in detail["cards"]:
            lines.append(f"### {item['ticker']}" + (f" — {item['company']}" if item.get("company") else ""))
            lines.append(plan(item))
            lines.append("Why: " + "; ".join(item["why"]) + ".")
            lines.append("Next: " + str(item["next"]))
        if detail["more"]:
            lines.append(f"- +{detail['more']} more on the website")
        if detail["unchanged"]:
            lines.append("- Unchanged: " + ", ".join(detail["unchanged"]))
        lines.append("")
    recovering = sections[SECTION_RECOVERING]
    lines.append("## Recovering (watch)")
    for item in recovering["cards"]:
        lines.append(f"- **{item['ticker']}** {item['position'] or ''} — {item['next']}")
    if recovering["more"] or recovering["unchanged"]:
        lines.append(f"- {recovering['more'] + len(recovering['unchanged'])} more on the website")
    lines.append("")
    blocked = sections[SECTION_BLOCKED]
    lines.append(f"## Blocked: {blocked['count']}")
    for item in blocked["changed"]:
        lines.append(f"- {item['ticker']} newly blocked: {', '.join(item['blockers']) or 'see record'}")
    if presentation["changes"]:
        lines += ["", "## What changed"]
        lines += [f"- {change['ticker']}: {change['text']}" for change in presentation["changes"]]
    if presentation.get("scorecard"):
        lines += ["", f"Scorecard: {presentation['scorecard']}"]
    lines += ["", presentation["disclaimer"]]
    return "\n".join(lines)
