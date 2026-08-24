from app.services.daily_changes import (
    attach_daily_changes,
    build_daily_changes,
    render_daily_changes,
)


def _candidate(
    ticker: str,
    status: str,
    distance: str,
    price: str,
    blockers: list[str],
    *,
    stop: str = "90",
    relative_strength: str = "1",
) -> dict:
    return {
        "ticker": ticker,
        "buyability_status": status,
        "distance_to_trigger_pct": distance,
        "current_price": price,
        "invalidation_price": stop,
        "buy_conditions": blockers,
        "payload": {"blocker_ids": blockers},
        "metrics": {
            "close": price,
            "relative_return_20d_vs_qqq_pct": relative_strength,
            "cash_runway_months": "18",
        },
    }


def test_daily_changes_compare_finalized_candidates_and_evidence(monkeypatch) -> None:
    prior = {
        "run_id": "prior-run",
        "as_of_date": "2026-08-20",
        "candidates": [
            _candidate(
                "IONQ", "RADAR", "9.2", "91", ["reclaim EMA20", "positive RS"],
                relative_strength="-2",
            ),
            _candidate("RKLB", "RADAR", "3", "76", ["hold stop"], stop="74.78"),
            _candidate("OLD", "RADAR", "12", "88", ["wait"]),
        ],
        "evidence": [],
    }
    monkeypatch.setattr(
        "app.services.daily_changes._previous_run", lambda _session, **_kwargs: prior
    )
    payload = {
        "strategy_key": "dynamic_swing_buy_alerts",
        "strategy_version": "0.7",
        "as_of_date": "2026-08-21",
        "candidates": [
            _candidate("IONQ", "ALMOST_READY", "4.5", "95.5", ["reclaim EMA20"]),
            _candidate(
                "RKLB",
                "NOT_ELIGIBLE",
                "8",
                "74",
                ["form a new base"],
                stop="70",
            ),
            _candidate("NEW", "RADAR", "9", "91", ["confirm volume"]),
        ],
        "evidence": [
            {
                "ticker": "IONQ",
                "evidence_type": "filing_review",
                "source_url": "https://example.test/10-q",
                "summary": "New ATM offering disclosed in the 10-Q",
            }
        ],
    }

    changes = build_daily_changes(object(), payload=payload)

    assert changes["new_candidates"] == ["NEW"]
    assert changes["removed_candidates"] == ["OLD"]
    assert changes["classification_changes"] == [
        {
            "ticker": "IONQ",
            "previous": "RADAR",
            "current": "ALMOST_READY",
            "direction": "promoted",
        },
        {
            "ticker": "RKLB",
            "previous": "RADAR",
            "current": "NOT_ELIGIBLE",
            "direction": "demoted",
        },
    ]
    assert changes["trigger_distance_changes"][0]["direction"] == "improved"
    assert changes["stop_breaches"] == [
        {"ticker": "RKLB", "current_price": "74", "prior_stop": "74.78"}
    ]
    assert changes["blocker_changes"][0] == {
        "ticker": "IONQ",
        "resolved": ["positive RS"],
        "introduced": [],
    }
    assert changes["evidence_changes"][0]["category"] == "dilution_or_financing"
    assert changes["fundamental_changes"] == []
    ionq_attention = next(
        item for item in changes["attention_today"] if item["ticker"] == "IONQ"
    )
    assert "relative_strength_turned_positive" in ionq_attention["reasons"]


def test_daily_changes_markdown_is_idempotently_attached() -> None:
    changes = {
        "baseline": False,
        "new_candidates": [],
        "classification_changes": [],
        "trigger_distance_changes": [],
        "stop_breaches": [],
        "blocker_changes": [],
        "fundamental_changes": [],
        "evidence_changes": [],
    }
    report = "# Daily alert\n\nNo trade today."

    once = attach_daily_changes(report, changes)
    twice = attach_daily_changes(once, changes)

    assert once == twice
    assert twice.count("## What changed since yesterday?") == 1
    assert "No material candidate" in render_daily_changes(changes)


def test_daily_changes_suppress_wording_only_and_undated_research(monkeypatch) -> None:
    prior = {
        "run_id": "prior-run",
        "as_of_date": "2026-08-20",
        "candidates": [
            _candidate("AAPL", "RADAR", "8", "92", ["Reclaim EMA20."])
        ],
        "evidence": [],
    }
    monkeypatch.setattr(
        "app.services.daily_changes._previous_run", lambda _session, **_kwargs: prior
    )
    current = _candidate("AAPL", "RADAR", "7", "93", ["Recover above EMA20."])
    current["payload"] = None
    prior["candidates"][0]["payload"] = None
    current["metrics"].pop("cash_runway_months")
    payload = {
        "strategy_key": "dynamic_swing_buy_alerts",
        "strategy_version": "0.7",
        "as_of_date": "2026-08-21",
        "candidates": [current],
        "evidence": [
            {
                "ticker": "AAPL",
                "evidence_type": "qualitative_research",
                "summary": "Offerings and dilution were reviewed; no new event identified.",
                "details": {"offerings_atm_convertibles_and_warrants": "reviewed"},
            }
        ],
    }

    changes = build_daily_changes(object(), payload=payload)

    assert changes["blocker_changes"] == []
    assert changes["fundamental_changes"] == []
    assert changes["evidence_changes"] == []
