from app.config import Settings
from app.services.daily_stock_alert_preparation import prepare_daily_stock_alert


def _feature(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "company": f"{ticker} Company",
        "sic_code": "3571",
        "close": "95.00",
        "daily_return_pct": "2.41",
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
                {"ticker": "MSFT", "payload": {"in_raw_pool": True}},
                {"ticker": "NVDA", "payload": {"in_raw_pool": True}},
            ],
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
    assert result["candidates"][0]["suggested_trigger_price"] == "100.10000"
    assert result["candidates"][0]["represented_gates"] == {
        "market_regime_gate_passed": True,
        "relative_strength_gate_passed": True,
        "price_at_or_above_trigger": False,
        "price_within_five_pct_below_trigger": False,
    }
    template = result["run_template"]
    assert template["strategy_key"] == "dynamic_swing_buy_alerts"
    assert template["decision_contract_version"] == "0.8"
    assert template["candidates"] == []
    assert template["report_markdown"] is None


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
