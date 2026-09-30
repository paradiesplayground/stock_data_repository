from app.services.daily_changes import (
    _previous_run,
    attach_daily_changes,
    build_daily_changes,
    render_daily_changes,
)


def test_previous_run_comparison_continues_across_strategy_versions(
    monkeypatch,
) -> None:
    captured = {}

    def list_runs(_session, **kwargs):
        captured.update(kwargs)
        return {"items": [{"run_id": "prior-v0.7-run"}]}

    monkeypatch.setattr("app.services.daily_changes.list_strategy_runs", list_runs)
    monkeypatch.setattr(
        "app.services.daily_changes.get_strategy_run",
        lambda _session, run_id: {"run_id": run_id, "strategy_version": "0.7"},
    )

    prior = _previous_run(
        object(),
        strategy_key="dynamic_swing_buy_alerts",
        as_of_date="2026-08-27",
    )

    assert prior["run_id"] == "prior-v0.7-run"
    assert "strategy_version" not in captured
    assert captured["end_date"] == "2026-08-26"


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
    assert "No material setup changes since the previous alert." in render_daily_changes(changes)


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


def test_daily_changes_curates_material_score_and_trigger_moves(monkeypatch) -> None:
    prior = {"run_id": "prior", "as_of_date": "2026-08-20", "candidates": [_candidate("KLIC", "RADAR", "2.8", "90", [])], "evidence": []}
    current = _candidate("KLIC", "RADAR", "0.1", "95", [])
    prior["candidates"][0]["setup_score"] = 71
    current["setup_score"] = 79
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={"strategy_key": "dynamic_swing_buy_alerts", "as_of_date": "2026-08-21", "candidates": [current], "evidence": []})
    rendered = render_daily_changes(changes)

    assert len(changes["meaningful_changes"]) <= 8
    assert "KLIC improved from 2.8% to 0.1% below trigger." in rendered
    assert "KLIC setup score improved from 71 to 79." in rendered


def test_daily_changes_formats_trigger_precision_and_ignores_research_only_downgrade(monkeypatch) -> None:
    prior_candidate = _candidate("KLIC", "RADAR", "15.11432217908248185792340544", "90", [])
    prior_candidate["payload"]["qualitative_evidence_status"] = "fresh_researched"
    current_candidate = _candidate("KLIC", "NOT_ELIGIBLE", "14.01432217908248185792340544", "90", [])
    current_candidate["payload"]["qualitative_evidence_status"] = "missing"
    prior = {"run_id": "prior", "as_of_date": "2026-08-20", "candidates": [prior_candidate], "evidence": []}
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={"strategy_key": "dynamic_swing_buy_alerts", "as_of_date": "2026-08-21", "candidates": [current_candidate], "evidence": []})
    rendered = render_daily_changes(changes)

    assert changes["classification_changes"] == []
    assert "15.1%" in rendered
    assert "14.0%" in rendered
    assert "15.114322" not in rendered


def test_klic_recovery_exit_is_prominent_and_not_a_generic_removal(monkeypatch) -> None:
    prior_candidate = _candidate("KLIC", "RADAR", "8", "88", [])
    prior_candidate["payload"].update({"in_raw_pool": True})
    prior_candidate["screen_bucket"] = "qualified"
    prior_candidate["metrics"].update({
        "price_change_12w_pct": "-22",
        "revenue_ttm_yoy_pct": "70",
        "latest_quarter_revenue_yoy_pct": "80",
        "avg_dollar_volume_20d": "100000000",
    })
    current_candidate = _candidate("KLIC", "NOT_ELIGIBLE", "4", "93.72", [])
    current_candidate["payload"].update({
        "in_raw_pool": False,
        "presentation": {"blockers": [{
            "gate": "Screen: 12-week price change",
            "reason": "12-week price change is -11.5%; required <= -20.0%.",
            "clear_condition": "12-week price change must be <= -20.0%.",
        }]},
    })
    current_candidate["screen_bucket"] = "dropped"
    current_candidate["metrics"].update({
        "price_change_12w_pct": "-11.5",
        "revenue_ttm_yoy_pct": "70",
        "latest_quarter_revenue_yoy_pct": "80",
        "avg_dollar_volume_20d": "100000000",
        "relative_return_20d_vs_qqq_pct": "14.1",
    })
    prior = {"run_id": "prior", "as_of_date": "2026-09-28", "candidates": [prior_candidate], "evidence": []}
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={
        "strategy_key": "dynamic_swing_buy_alerts",
        "as_of_date": "2026-09-29",
        "candidates": [current_candidate],
        "evidence": [],
    })

    assert changes["recovery_exits"] == [{
        "ticker": "KLIC",
        "previous_decline_pct": "-22",
        "current_decline_pct": "-11.5",
    }]
    assert changes["events"][0]["event_type"] == "LEFT_DISCOVERY_DUE_TO_RECOVERY"
    assert "RECOVERY_PROGRESS" in changes["events"][0]["event_types"]
    assert changes["report_sections"]["needs_attention_today"][0]["ticker"] == "KLIC"
    assert "recovered out of the original 12-week decline screen" in render_daily_changes(changes)
    assert changes["report_sections"]["needs_attention_today"][0]["what_matters_next"] == "Continue tracking the recovery; watch for breakout confirmation and remaining entry gates."


def test_recovery_exit_is_not_labeled_recovery_when_another_hard_failure_remains(monkeypatch) -> None:
    prior_candidate = _candidate("KLIC", "RADAR", "8", "88", [])
    prior_candidate["payload"].update({"in_raw_pool": True})
    prior_candidate["screen_bucket"] = "qualified"
    current_candidate = _candidate("KLIC", "NOT_ELIGIBLE", "4", "93", [])
    current_candidate["payload"].update({
        "in_raw_pool": False,
        "presentation": {"blockers": [
            {"gate": "Screen: 12-week price change", "reason": "12-week price change is -11%; required <= -20.0%.", "clear_condition": "12-week price change must be <= -20.0%."},
            {"gate": "Deterministic risk review", "reason": "share count growth at or above 15 pct.", "clear_condition": "Year-over-year share-count growth must be below 15.0%."},
        ]},
    })
    current_candidate["screen_bucket"] = "dropped"
    current_candidate["deterministic_risk_flags"] = ["share_count_growth_at_or_above_15_pct"]
    current_candidate["metrics"]["price_change_12w_pct"] = "-11"
    prior = {"run_id": "prior", "as_of_date": "2026-09-28", "candidates": [prior_candidate], "evidence": []}
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={
        "strategy_key": "dynamic_swing_buy_alerts", "as_of_date": "2026-09-29",
        "candidates": [current_candidate], "evidence": [],
    })

    assert changes["recovery_exits"] == []
    assert all(event["event_type"] != "LEFT_DISCOVERY_DUE_TO_RECOVERY" for event in changes["events"])


def test_attention_sort_prefers_closer_trigger_before_ticker(monkeypatch) -> None:
    prior = {
        "run_id": "prior", "as_of_date": "2026-09-28",
        "candidates": [_candidate("FAR", "RADAR", "12", "88", []), _candidate("CLOSE", "RADAR", "12", "88", [])],
        "evidence": [],
    }
    current = [_candidate("FAR", "ALMOST_READY", "9", "91", []), _candidate("CLOSE", "ALMOST_READY", "0.5", "99.5", [])]
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={
        "strategy_key": "dynamic_swing_buy_alerts", "as_of_date": "2026-09-29",
        "candidates": current, "evidence": [],
    })

    assert [item["ticker"] for item in changes["report_sections"]["needs_attention_today"]] == ["CLOSE", "FAR"]


def test_archived_ticker_is_in_concise_report(monkeypatch) -> None:
    archived = _candidate("OLD", "RADAR", "12", "88", [])
    prior = {"run_id": "prior", "as_of_date": "2026-09-28", "candidates": [archived], "evidence": []}
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={
        "strategy_key": "dynamic_swing_buy_alerts", "as_of_date": "2026-09-29",
        "candidates": [], "evidence": [],
    })
    report = render_daily_changes(changes)

    assert changes["report_sections"]["new_materially_changed"][0]["ticker"] == "OLD"
    assert "OLD was archived because it no longer met the tracked screen." in report


def test_report_next_step_uses_specific_event_action(monkeypatch) -> None:
    prior = {"run_id": "prior", "as_of_date": "2026-09-28", "candidates": [_candidate("VOL", "RADAR", "12", "88", [])], "evidence": []}
    current = _candidate("VOL", "RADAR", "4", "94", ["wait for volume confirmation"])
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={
        "strategy_key": "dynamic_swing_buy_alerts", "as_of_date": "2026-09-29",
        "candidates": [current], "evidence": [],
    })

    item = changes["report_sections"]["needs_attention_today"][0]
    assert item["what_matters_next"] == "Confirm breakout above the current trigger."


def test_rendered_sections_consolidate_events_and_keep_recovery_watch_separate(monkeypatch) -> None:
    prior_candidate = _candidate("KLIC", "RADAR", "9", "88", [], relative_strength="-2")
    prior_candidate["metrics"].update({
        "price_change_12w_pct": "-18", "revenue_ttm_yoy_pct": "70",
        "latest_quarter_revenue_yoy_pct": "80", "avg_dollar_volume_20d": "100000000",
    })
    current_candidate = _candidate("KLIC", "RADAR", "8", "89", [], relative_strength="2")
    current_candidate["metrics"].update({
        "price_change_12w_pct": "-17", "revenue_ttm_yoy_pct": "70",
        "latest_quarter_revenue_yoy_pct": "80", "avg_dollar_volume_20d": "100000000",
    })
    prior = {"run_id": "prior", "as_of_date": "2026-09-28", "candidates": [prior_candidate], "evidence": []}
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={
        "strategy_key": "dynamic_swing_buy_alerts", "as_of_date": "2026-09-29",
        "candidates": [current_candidate], "evidence": [],
    })
    rendered = render_daily_changes(changes)

    assert changes["report_sections"]["needs_attention_today"] == []
    assert [item["ticker"] for item in changes["report_sections"]["recovery_watch"]] == ["KLIC"]
    assert rendered.count("| KLIC |") == 1
    assert "## What changed since yesterday?" in rendered
    assert "### Recovery Watch" in rendered
    assert "Why:" not in rendered
    assert "Blocking gates" not in rendered


def test_needs_attention_is_capped_at_eight_names(monkeypatch) -> None:
    tickers = [f"TICK{i:02d}" for i in range(9)]
    prior = {
        "run_id": "prior",
        "as_of_date": "2026-09-28",
        "candidates": [_candidate(ticker, "RADAR", "12", "88", []) for ticker in tickers],
        "evidence": [],
    }
    current = [_candidate(ticker, "RADAR", "5", "95", []) for ticker in tickers]
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={
        "strategy_key": "dynamic_swing_buy_alerts", "as_of_date": "2026-09-29",
        "candidates": current, "evidence": [],
    })

    assert len(changes["report_sections"]["needs_attention_today"]) == 8
    assert [item["ticker"] for item in changes["report_sections"]["needs_attention_today"]] == tickers[:8]


def test_repeated_setup_invalidation_breach_is_suppressed(monkeypatch) -> None:
    prior_candidate = _candidate("KLIC", "RADAR", "4", "89", [], stop="90")
    current_candidate = _candidate("KLIC", "RADAR", "4", "88", [], stop="90")
    prior = {"run_id": "prior", "as_of_date": "2026-09-28", "candidates": [prior_candidate], "evidence": []}
    monkeypatch.setattr("app.services.daily_changes._previous_run", lambda *_args, **_kwargs: prior)

    changes = build_daily_changes(object(), payload={
        "strategy_key": "dynamic_swing_buy_alerts",
        "as_of_date": "2026-09-29",
        "candidates": [current_candidate],
        "evidence": [],
    })

    assert changes["stop_breaches"] == []
    assert changes["events"] == []
