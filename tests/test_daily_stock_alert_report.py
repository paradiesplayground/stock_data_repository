from app.services.daily_stock_alert_report import (
    report_enrichment,
    report_focus_sort_key,
    structural_targets,
    ticker_specific_explanation,
)


def _snapshot() -> dict:
    return {
        "ticker": "KLIC",
        "suggested_trigger_price": "100",
        "suggested_invalidation_price": "90",
        "distance_to_trigger_pct": "0.1",
        "deterministic_risk_flags": [],
        "deterministic_metrics": {
            "close": "99.9", "high_60d": "130", "drawdown_52w_pct": "-37.5625", "drawdown_12w_high_pct": "-25",
            "revenue_ttm_yoy_pct": "70", "latest_quarter_revenue_yoy_pct": "80",
            "relative_return_20d_vs_qqq_pct": "8", "relative_volume_20d": "1.2",
            "avg_dollar_volume_20d": "100000000",
        },
    }


def test_report_enrichment_scores_every_candidate_and_uses_two_observed_target_levels() -> None:
    enrichment = report_enrichment(snapshot=_snapshot(), plan={"evidence_state": "missing"})

    assert 0 <= enrichment["setup_score"] <= 100
    assert enrichment["structural_targets"] == [
        {"price": "130", "basis": "60-day resistance", "r_multiple": "3.00"},
        {"price": "160.00000000", "basis": "52-week resistance", "r_multiple": "6.00"},
    ]


def test_structural_targets_dedupe_same_60d_and_52w_high() -> None:
    assert structural_targets(
        trigger="100",
        metrics={"close": "100", "high_60d": "150", "drawdown_52w_pct": "-33.33333333"},
    ) == [{"price": "150", "basis": "60-day resistance"}]


def test_report_enrichment_does_not_manufacture_fixed_r_targets() -> None:
    snapshot = _snapshot()
    snapshot["deterministic_metrics"].update({
        "high_60d": "99",
        "drawdown_52w_pct": "0",
    })

    assert report_enrichment(snapshot=snapshot, plan={})["structural_targets"] == []


def test_report_focus_rank_favors_score_then_trigger_proximity() -> None:
    candidates = [
        {"ticker": "FAR", "setup_score": 82, "distance_to_trigger_pct": "4"},
        {"ticker": "CLOSE", "setup_score": 82, "distance_to_trigger_pct": "0.2"},
        {"ticker": "LOW", "setup_score": 70, "distance_to_trigger_pct": "0.1"},
    ]

    assert [item["ticker"] for item in sorted(candidates, key=report_focus_sort_key)] == ["CLOSE", "FAR", "LOW"]


def test_ticker_explanation_uses_snapshot_facts_not_workflow_boilerplate() -> None:
    snapshot = _snapshot()
    candidate = {**snapshot, "metrics": snapshot["deterministic_metrics"], "trigger_price": "100", "invalidation_price": "90", "primary_risk": "cash runway unavailable"}

    why, next_condition = ticker_specific_explanation(candidate)

    assert "20-day relative strength" in why
    assert "latest-quarter revenue growth" in why
    assert "cash runway unavailable" in why
    assert "$100.00" in next_condition
    assert "$90.00" in next_condition
