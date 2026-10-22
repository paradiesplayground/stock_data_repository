"""Offline driver for the v0.9 golden replay (shared by the test and by manual inspection).

The fixture holds production feature calculation 1.6.0 rows for 19 tickers,
the SPY market gate, and the saved research blockers, so the replay uses
exactly the same per-day code path as the shadow job (``advance_day``).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.services.alert_v09_engine import EngineConfig, Research, load_configuration, passes_base_gates
from app.services.blocker_codes import blocker_codes
from app.services.daily_stock_alert_shadow import _sessions_counter, advance_day

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v09_golden"
TRACKING_START = date(2026, 7, 1)


def _load() -> tuple[dict[date, dict[str, dict[str, Any]]], dict[date, bool], list[tuple[date, str, list[str]]]]:
    by_date: dict[date, dict[str, dict[str, Any]]] = defaultdict(dict)
    with (FIXTURE / "features.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            item = {key: (value if value != "" else None) for key, value in row.items()}
            item["listing_ok"] = item["listing_ok"] == "t"
            by_date[date.fromisoformat(item.pop("as_of_date"))][item["ticker"]] = item
    with (FIXTURE / "market.csv").open(encoding="utf-8") as handle:
        market = {date.fromisoformat(row["as_of_date"]): row["market_gate_passed"] == "t" for row in csv.DictReader(handle)}
    with (FIXTURE / "research.csv").open(encoding="utf-8") as handle:
        research = [
            (date.fromisoformat(row["as_of_date"]), row["ticker"], json.loads(row["blockers"]))
            for row in csv.DictReader(handle)
        ]
    return by_date, market, research


def replay(start: date = TRACKING_START) -> dict[date, list[dict[str, Any]]]:
    configuration = load_configuration()
    config = EngineConfig.from_configuration(configuration)
    by_date, market, research_rows = _load()
    sessions = sorted(by_date)
    sessions_between = _sessions_counter(sessions)
    states: dict[str, dict[str, Any] | None] = {}
    output: dict[date, list[dict[str, Any]]] = {}
    for market_date in sessions:
        if market_date < start:
            continue
        research: dict[str, Research] = {}
        for research_date, ticker, blockers in research_rows:
            if market_date - timedelta(days=config.research_max_age_days) <= research_date <= market_date:
                research[ticker] = Research(research_date, tuple(blocker_codes(blockers)))
        items = by_date[market_date]
        listed = {ticker for ticker, item in items.items() if item["listing_ok"] and passes_base_gates(item, config, True)}
        tracked = {ticker for ticker, state in states.items() if state and not state.get("exited")}
        day_items = {ticker: item for ticker, item in items.items() if ticker in listed or ticker in tracked}
        candidates, next_states, _ = advance_day(
            day_items, listed, states, as_of_date=market_date, market_ok=market[market_date],
            research=research, sessions_between=sessions_between, config=config,
            configuration=configuration,
        )
        states.update(next_states)
        output[market_date] = candidates
    return output


def section_map(output: dict[date, list[dict[str, Any]]], since: date) -> dict[str, dict[str, str]]:
    return {
        market_date.isoformat(): {item["ticker"]: item["payload"]["section"] for item in candidates}
        for market_date, candidates in output.items()
        if market_date >= since
    }


if __name__ == "__main__":
    result = replay()
    for market_date, candidates in result.items():
        if market_date < date(2026, 9, 28):
            continue
        cells = sorted(
            f"{item['ticker']}:{item['payload']['section'][:4]}"
            + (f"({float(item['distance_to_trigger_pct']):+.1f})" if item.get("distance_to_trigger_pct") else "")
            for item in candidates
        )
        print(market_date, " ".join(cells))
