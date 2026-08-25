from app.config import Settings
from app.services.daily_stock_alert_preparation import (
    _deterministic_candidate,
    _research_plan,
    prepare_daily_stock_alert,
)


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
    assert result["strategy_version"] == "0.7"
    assert result["comparison"]["new_tickers"] == ["AAPL"]
    assert result["comparison"]["continuing_tickers"] == ["MSFT"]
    assert result["comparison"]["dropped_tickers"] == ["NVDA"]
    assert result["skill_version"] == "1.5.3"
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
    assert template["decision_contract_version"] == "0.8"
    assert template["candidates"] == []
    assert template["report_markdown"] is None
    assert template["summary"]["preparation_scope"] == {
        "current_raw_tickers": ["AAPL", "MSFT"],
        "dropped_reassessed_tickers": ["NVDA"],
        "expected_candidate_tickers": ["AAPL", "MSFT", "NVDA"],
        "company_names": {
            "AAPL": "AAPL Company",
            "MSFT": "MSFT Company",
        },
    }


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
        "metrics": dict(feature),
    }
    evidence = [{"ticker": "MSFT", "evidence_type": "filing_review"}]

    plan = _research_plan(candidate, prior, evidence, is_new=False)

    assert plan["priority"] == "low"
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

    assert plan["priority"] == "high"
    assert "material_daily_move" in plan["reasons"]


def test_research_plan_does_not_escalate_generic_quality_flag() -> None:
    feature = _feature("MSFT")
    feature["quality_flags"] = ["free_cash_flow_annual_only"]
    candidate = _deterministic_candidate(feature, True)
    prior = {
        "ticker": "MSFT",
        "buyability_status": "RADAR",
        "metrics": dict(feature),
    }

    plan = _research_plan(
        candidate,
        prior,
        [{"ticker": "MSFT", "evidence_type": "filing_review"}],
        is_new=False,
    )

    assert candidate["deterministic_risk_flags"] == []
    assert candidate["repository_quality_flags"] == ["free_cash_flow_annual_only"]
    assert plan["priority"] == "low"


def test_research_plan_does_not_invent_fresh_filing_without_prior_date() -> None:
    feature = _feature("MSFT")
    candidate = _deterministic_candidate(feature, True)
    prior = {
        "ticker": "MSFT",
        "buyability_status": "RADAR",
        "metrics": {**feature, "latest_source_filing_date": None},
    }

    plan = _research_plan(
        candidate,
        prior,
        [{"ticker": "MSFT", "evidence_type": "filing_review"}],
        is_new=False,
    )

    assert "fresh_source_filing" not in plan["reasons"]
    assert plan["priority"] == "low"
