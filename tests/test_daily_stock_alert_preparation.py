from datetime import date

from app.config import Settings
from app.services.daily_stock_alert_preparation import (
    _deterministic_candidate,
    _prior_run,
    _research_plan,
    prepare_daily_stock_alert,
)
from app.services.strategy_tracking import configuration_fingerprint
from app.services.daily_stock_alert import _validate_prepared_scope


def test_preparation_carries_prior_scope_across_strategy_versions(monkeypatch) -> None:
    captured = {}

    def list_runs(_session, **kwargs):
        captured.update(kwargs)
        return {"items": [{"run_id": "prior-v0.7-run"}]}

    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.list_strategy_runs", list_runs
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.get_strategy_run",
        lambda _session, run_id: {"run_id": run_id, "strategy_version": "0.7"},
    )

    prior = _prior_run(object(), date(2026, 8, 27))

    assert prior["run_id"] == "prior-v0.7-run"
    assert "strategy_version" not in captured
    assert captured["end_date"] == "2026-08-26"


def _feature(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "company": f"{ticker} Company",
        "sic_code": "3571",
        "close": "95.00",
        "daily_return_pct": "2.41",
        "latest_source_filing_date": "2026-08-20",
        "high_20d": "100.00",
        "low_20d": "88.00",
        "atr_14": "4.00",
        "relative_return_20d_vs_qqq_pct": "3.00",
        "cash_runway_months": "18",
        "free_cash_flow_ttm": "-100",
        "share_count_yoy_pct": "5",
        "quality_flags": [],
        "source_data_cutoff_utc": "2026-08-21T22:00:00+00:00",
    }


def test_prepare_daily_alert_builds_deterministic_hybrid_handoff(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.get_data_freshness",
        lambda _session, _settings: {
            "expected_market_date": "2026-08-21",
            "ready_for_screening": True,
            "freshness_issues": [],
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.query_security_features",
        lambda _session, **_kwargs: {
            "as_of_date": "2026-08-21",
            "calculation_version": "1.5.0",
            "count": 2,
            "items": [_feature("AAPL"), _feature("MSFT")],
            "excluded_industry_groups": [
                {"key": "curated:healthcare", "label": "Healthcare"}
            ],
            "excluded_sic_prefixes": ["283", "384"],
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation._spy_market_regime",
        lambda _session, _date: {
            "benchmark_ticker": "SPY",
            "status": "pass",
            "gate_passed": True,
            "latest_close": "650",
            "sma_50": "640",
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation._prior_run",
        lambda _session, _date: {
            "run_id": "prior-run",
            "as_of_date": "2026-08-20",
            "candidates": [
                {
                    "ticker": "MSFT",
                    "buyability_status": "RADAR",
                    "metrics": {
                        "close": "94.00",
                        "latest_source_filing_date": "2026-08-19",
                    },
                    "payload": {"in_raw_pool": True},
                },
                {"ticker": "NVDA", "payload": {"in_raw_pool": True}},
            ],
            "evidence": [
                {
                    "ticker": "MSFT",
                    "evidence_type": "filing_review",
                    "summary": "No going-concern language found.",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.get_security_features",
        lambda _session, ticker, **_kwargs: {
            "ticker": ticker,
            "found": True,
            "features_available": True,
        },
    )

    result = prepare_daily_stock_alert(
        object(), Settings(), as_of_date="2026-08-21"
    )

    assert result["status"] == "prepared"
    assert result["strategy_version"] == "0.8"
    assert result["comparison"]["new_tickers"] == ["AAPL"]
    assert result["comparison"]["continuing_tickers"] == ["MSFT"]
    assert result["comparison"]["dropped_tickers"] == ["NVDA"]
    assert result["skill_version"] == "1.6.0"
    assert result["candidates"][0]["suggested_trigger_price"] == "100.10000"
    assert result["candidates"][0]["represented_gates"] == {
        "market_regime_gate_passed": True,
        "relative_strength_gate_passed": True,
        "price_at_or_above_trigger": False,
        "price_within_five_pct_below_trigger": False,
    }
    queue = {item["ticker"]: item for item in result["research_queue"]}
    assert queue["AAPL"]["priority"] == "high"
    assert "new_raw_pool_candidate" in queue["AAPL"]["reasons"]
    assert queue["MSFT"]["priority"] == "high"
    assert "fresh_source_filing" in queue["MSFT"]["reasons"]
    assert queue["MSFT"]["reusable_prior_evidence"][0]["evidence_type"] == (
        "filing_review"
    )
    assert result["dropped_candidate_reviews"][0]["ticker"] == "NVDA"
    assert result["dropped_candidate_reviews"][0]["current_feature_check"][
        "features_available"
    ] is True
    template = result["run_template"]
    assert template["strategy_key"] == "dynamic_swing_buy_alerts"
    assert template["strategy_version"] == "0.8"
    assert template["configuration"]["strategy"] == {
        "key": "dynamic_swing_buy_alerts",
        "version": "0.8",
        "name": "Dynamic swing buy alerts",
        "skill_version": "1.6.0",
    }
    assert result["strategy_configuration_fingerprint"] == configuration_fingerprint(
        template["configuration"]
    )
    assert template["decision_contract_version"] == "0.8"
    assert template["candidates"] == []
    assert template["report_markdown"] is None
    scope = template["summary"]["preparation_scope"]
    assert scope["current_raw_tickers"] == ["AAPL", "MSFT"]
    assert scope["dropped_reassessed_tickers"] == ["NVDA"]
    assert scope["expected_candidate_tickers"] == ["AAPL", "MSFT", "NVDA"]
    assert scope["company_names"] == {"AAPL": "AAPL Company", "MSFT": "MSFT Company"}
    assert scope["research_scope_by_ticker"]["NVDA"]["research_scope"] == "deterministic_only"


def test_prepare_daily_alert_rejects_stale_data(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.get_data_freshness",
        lambda _session, _settings: {
            "expected_market_date": "2026-08-21",
            "ready_for_screening": False,
            "freshness_issues": ["derived_features latest run failed"],
        },
    )

    try:
        prepare_daily_stock_alert(object(), Settings(), as_of_date="2026-08-21")
    except RuntimeError as error:
        assert "derived_features latest run failed" in str(error)
    else:
        raise AssertionError("stale preparation must fail")


def test_prepare_daily_alert_requires_feature_v140_or_later(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.get_data_freshness",
        lambda _session, _settings: {
            "expected_market_date": "2026-08-21",
            "ready_for_screening": True,
            "freshness_issues": [],
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.query_security_features",
        lambda _session, **_kwargs: {
            "as_of_date": "2026-08-21",
            "calculation_version": "1.3.9",
            "count": 0,
            "items": [],
            "excluded_industry_groups": [],
            "excluded_sic_prefixes": [],
        },
    )

    try:
        prepare_daily_stock_alert(object(), Settings(), as_of_date="2026-08-21")
    except RuntimeError as error:
        assert "1.4.0 or later" in str(error)
    else:
        raise AssertionError("old feature versions must fail")


def test_research_plan_deprioritizes_unchanged_candidate_with_prior_evidence() -> None:
    feature = _feature("MSFT")
    candidate = _deterministic_candidate(feature, True)
    prior = {
        "ticker": "MSFT",
        "buyability_status": "RADAR",
        "as_of_date": "2026-08-20",
        "metrics": dict(feature),
    }
    evidence = [{"ticker": "MSFT", "evidence_type": "filing_review"}]

    plan = _research_plan(candidate, prior, evidence, is_new=False, as_of_date=date(2026, 8, 21))

    assert plan["priority"] == "low"
    assert plan["research_scope"] == "carry_forward"
    assert plan["reasons"] == ["unchanged_candidate_review"]


def test_research_plan_escalates_material_daily_move() -> None:
    feature = _feature("MSFT")
    feature["daily_return_pct"] = "-7.25"
    candidate = _deterministic_candidate(feature, True)

    plan = _research_plan(
        candidate,
        {"ticker": "MSFT", "buyability_status": "RADAR", "metrics": feature},
        [{"ticker": "MSFT", "evidence_type": "filing_review"}],
        is_new=False,
    )

    assert plan["research_scope"] == "deterministic_only"
    assert "material_daily_move" not in plan["reasons"]


def test_research_plan_does_not_escalate_generic_quality_flag() -> None:
    feature = _feature("MSFT")
    feature["quality_flags"] = ["free_cash_flow_annual_only"]
    candidate = _deterministic_candidate(feature, True)
    prior = {
        "ticker": "MSFT", "as_of_date": "2026-08-20", "buyability_status": "RADAR",
        "metrics": dict(feature),
    }

    plan = _research_plan(
        candidate,
        prior,
        [{"ticker": "MSFT", "evidence_type": "filing_review", "retrieved_at_utc": "2026-08-20T12:00:00+00:00"}],
        is_new=False, as_of_date=date(2026, 8, 21),
    )

    assert candidate["deterministic_risk_flags"] == []
    assert candidate["repository_quality_flags"] == ["free_cash_flow_annual_only"]
    assert plan["priority"] == "low"


def test_research_plan_does_not_invent_fresh_filing_without_prior_date() -> None:
    feature = _feature("MSFT")
    candidate = _deterministic_candidate(feature, True)
    prior = {
        "ticker": "MSFT", "as_of_date": "2026-08-20", "buyability_status": "RADAR",
        "metrics": {**feature, "latest_source_filing_date": None},
    }

    plan = _research_plan(
        candidate,
        prior,
        [{"ticker": "MSFT", "evidence_type": "filing_review", "retrieved_at_utc": "2026-08-20T12:00:00+00:00"}],
        is_new=False, as_of_date=date(2026, 8, 21),
    )

    assert "fresh_source_filing" not in plan["reasons"]
    assert plan["priority"] == "low"


def test_deterministic_risk_flag_alone_does_not_require_deep_research() -> None:
    feature = _feature("MSFT")
    feature["cash_runway_months"] = "8"
    candidate = _deterministic_candidate(feature, True)

    plan = _research_plan(candidate, None, [], is_new=False, as_of_date=date(2026, 8, 21))

    assert "deterministic_risk_flag_deterministic_only" in plan["reasons"]
    assert plan["research_scope"] == "deterministic_only"


def test_unchanged_not_eligible_without_evidence_uses_deterministic_handling() -> None:
    feature = _feature("MSFT")
    feature["relative_return_20d_vs_qqq_pct"] = "-4"
    candidate = _deterministic_candidate(feature, True)
    prior = {"ticker": "MSFT", "buyability_status": "NOT_ELIGIBLE", "as_of_date": "2026-08-20", "metrics": dict(feature)}

    plan = _research_plan(candidate, prior, [], is_new=False, as_of_date=date(2026, 8, 21))

    assert plan["research_scope"] == "deterministic_only"


def test_prior_near_buyable_and_new_candidates_get_deep_research_priority() -> None:
    candidate = _deterministic_candidate(_feature("MSFT"), True)
    near = _research_plan(candidate, {"ticker": "MSFT", "buyability_status": "ALMOST_READY", "as_of_date": "2026-08-20", "metrics": _feature("MSFT")}, [], is_new=False, as_of_date=date(2026, 8, 21))
    new = _research_plan(candidate, None, [], is_new=True, as_of_date=date(2026, 8, 21))

    assert near["research_scope"] == "deep_research"
    assert new["research_scope"] == "deep_research"


def test_newer_sec_filing_invalidates_reusable_evidence() -> None:
    feature = _feature("MSFT")
    candidate = _deterministic_candidate(feature, True)
    prior = {"ticker": "MSFT", "buyability_status": "RADAR", "as_of_date": "2026-08-20", "metrics": {**feature, "latest_source_filing_date": "2026-08-19"}}

    plan = _research_plan(candidate, prior, [{"ticker": "MSFT", "retrieved_at_utc": "2026-08-20T12:00:00+00:00"}], is_new=False, as_of_date=date(2026, 8, 21))

    assert "fresh_source_filing" in plan["reasons"]
    assert plan["research_scope"] == "deep_research"


def test_dropped_ticker_is_not_automatically_deep_research() -> None:
    feature = _feature("MSFT")
    feature["relative_return_20d_vs_qqq_pct"] = "-1"
    plan = _research_plan(_deterministic_candidate(feature, True), {"ticker": "MSFT", "buyability_status": "NOT_ELIGIBLE", "as_of_date": "2026-08-20", "metrics": feature}, [], is_new=False, is_dropped=True, as_of_date=date(2026, 8, 21))

    assert "dropped_from_raw_pool" in plan["reasons"]
    assert plan["research_scope"] == "deterministic_only"


def _prepare_many(monkeypatch, *, count: int, regime: str) -> dict:
    monkeypatch.setattr("app.services.daily_stock_alert_preparation.get_data_freshness", lambda *_args: {"expected_market_date": "2026-08-21", "ready_for_screening": True, "freshness_issues": []})
    monkeypatch.setattr("app.services.daily_stock_alert_preparation.query_security_features", lambda *_args, **_kwargs: {"as_of_date": "2026-08-21", "calculation_version": "1.5.0", "count": count, "items": [_feature(f"T{index:02d}") for index in range(count)], "excluded_industry_groups": [], "excluded_sic_prefixes": []})
    monkeypatch.setattr("app.services.daily_stock_alert_preparation._spy_market_regime", lambda *_args: {"benchmark_ticker": "SPY", "status": regime, "gate_passed": regime == "pass"})
    monkeypatch.setattr("app.services.daily_stock_alert_preparation._prior_run", lambda *_args: None)
    return prepare_daily_stock_alert(object(), Settings(), as_of_date="2026-08-21")


def test_normal_and_block_research_budgets_and_report_scope(monkeypatch) -> None:
    normal = _prepare_many(monkeypatch, count=25, regime="pass")
    blocked = _prepare_many(monkeypatch, count=25, regime="block")

    assert len(normal["deep_research_queue"]) == 12
    assert len(blocked["deep_research_queue"]) == 6
    assert len(normal["run_template"]["summary"]["preparation_scope"]["expected_candidate_tickers"]) == 25
    assert len(normal["report_scope"]["detailed_tickers"]) == 20
    assert normal["report_scope"]["compact_summary_tickers"]


def test_unresearched_candidate_cannot_become_actionable() -> None:
    payload = {
        "strategy_key": "dynamic_swing_buy_alerts", "strategy_version": "0.8", "report_markdown": "done",
        "summary": {"preparation_scope": {"expected_candidate_tickers": ["MSFT"], "research_scope_by_ticker": {"MSFT": {"evidence_state": "missing", "requires_current_qualitative_confirmation_for_actionable_status": True}}}},
        "evidence": [],
        "candidates": [{"ticker": "MSFT", "buyability_status": "BUY_NOW", "payload": {"qualitative_evidence_status": "missing"}}],
    }

    try:
        _validate_prepared_scope(payload)
    except ValueError as error:
        assert "requires fresh qualitative evidence" in str(error)
    else:
        raise AssertionError("unresearched candidate must not become actionable")
