import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.strategy_decisions import decision_sort_key, normalize_candidate_decision


def _buy_setup(**overrides):
    payload = {
        "decision_status": "BUY_SETUP",
        "status_reason": "All entry and risk gates passed.",
        "next_condition": "Enter only while price remains inside the planned buy zone.",
        "current_entry": "10.25",
        "pct_above_trigger": "4.50",
        "t1_r": "1.00",
        "t2_r": "1.75",
        "technical_gate_passed": True,
        "market_regime_gate_passed": True,
    }
    payload.update(overrides)
    return payload


def _v08_candidate(status="ALMOST_READY", **overrides):
    payload = {
        "ticker": "AAPL",
        "screen_bucket": "qualified",
        "technical_state": "near_trigger",
        "buyability_status": status,
        "status_reason": "One represented gate remains.",
        "buy_conditions": ["Reach the trigger."],
        "remaining_gate_count": 1,
        "current_price": "95.00",
        "trigger_price": "100.00",
        "distance_to_trigger_pct": "5.00",
        "invalidation_price": "90.00",
        "technical_gate_passed": True,
        "market_regime_gate_passed": True,
        "metrics": {
            "close": "95.00",
            "relative_return_20d_vs_qqq_pct": "2.50",
            "relative_volume_20d": "1.10",
        },
    }
    payload.update(overrides)
    if "current_price" in overrides and "metrics" not in overrides:
        payload["metrics"] = {
            **payload["metrics"],
            "close": overrides["current_price"],
        }
    return payload


def test_buy_setup_accepts_exact_v07_minimum_gates() -> None:
    decision = normalize_candidate_decision(_buy_setup())

    assert decision["decision_status"] == "BUY_SETUP"
    assert decision["pct_above_trigger"] == Decimal("4.50")
    assert decision["t1_r"] == Decimal("1.00")
    assert decision["t2_r"] == Decimal("1.75")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"t1_r": "0.99"}, "t1_r >= 1.00"),
        ({"t2_r": "1.74"}, "t2_r >= 1.75"),
        ({"pct_above_trigger": "5.01"}, "pct_above_trigger <= 5.00"),
        ({"technical_gate_passed": False}, "technical_gate_passed=true"),
        ({"market_regime_gate_passed": False}, "market_regime_gate_passed=true"),
    ],
)
def test_buy_setup_rejects_any_failed_entry_gate(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_candidate_decision(_buy_setup(**overrides))


def test_every_decision_requires_explicit_reason_and_next_condition() -> None:
    with pytest.raises(ValueError, match="status_reason is required"):
        normalize_candidate_decision(
            {
                "decision_status": "WATCH",
                "status_reason": "",
                "next_condition": "Wait for confirmation.",
            }
        )

    with pytest.raises(ValueError, match="next_condition is required"):
        normalize_candidate_decision(
            {
                "decision_status": "AVOID",
                "status_reason": "Reward/risk is unacceptable.",
                "next_condition": "",
            }
        )


def test_decision_sorting_puts_buy_setup_before_higher_scoring_watch() -> None:
    buy = decision_sort_key("BUY_SETUP", Decimal("80"), "BUY")
    watch = decision_sort_key("WATCH", Decimal("99"), "WATCH")

    assert buy < watch


def test_v07_contract_requires_separate_screen_and_technical_states() -> None:
    with pytest.raises(ValueError, match="screen_bucket"):
        normalize_candidate_decision(
            {
                "decision_status": "WATCH",
                "status_reason": "The base is still developing.",
                "next_condition": "Reclaim both short EMAs.",
                "technical_state": "developing",
            },
            contract_required=True,
        )

    with pytest.raises(ValueError, match="technical_state"):
        normalize_candidate_decision(
            {
                "screen_bucket": "qualified",
                "decision_status": "WATCH",
                "status_reason": "The base is still developing.",
                "next_condition": "Reclaim both short EMAs.",
            },
            contract_required=True,
        )


def test_august_19_fixture_passes_full_v07_evidence_validation() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "august_19_v07.json").read_text()
    )

    assert fixture["run_id"] == "cceea193-6617-47a1-8605-a17a1ccc57df"
    assert fixture["summary"]["buy_setup_count"] == 0
    for candidate in fixture["candidates"]:
        normalize_candidate_decision(candidate, contract_required=True)


def test_v07_rejects_inconsistent_trade_plan_math() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "august_19_v07.json").read_text()
    )
    fly = next(item for item in fixture["candidates"] if item["ticker"] == "FLY")
    fly["trade_plan"]["potential_rewards"][0] = 999

    with pytest.raises(ValueError, match=r"potential_rewards\[0\].*inconsistent"):
        normalize_candidate_decision(fly, contract_required=True)


def test_v07_rejects_missing_price_relative_strength_and_volume() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "august_19_v07.json").read_text()
    )
    fly = next(item for item in fixture["candidates"] if item["ticker"] == "FLY")

    for field in ("close", "relative_return_20d_vs_qqq_pct", "relative_volume_20d"):
        candidate = {**fly, "metrics": {**fly["metrics"]}}
        candidate["metrics"].pop(field)
        with pytest.raises(ValueError, match=field):
            normalize_candidate_decision(candidate, contract_required=True)


def test_v08_almost_ready_accepts_exactly_one_represented_price_gate() -> None:
    decision = normalize_candidate_decision(
        _v08_candidate(), contract_version="0.8"
    )

    assert decision["buyability_status"] == "ALMOST_READY"
    assert decision["remaining_gate_count"] == 1


def test_v08_buy_now_rejects_any_remaining_gate() -> None:
    with pytest.raises(ValueError, match="BUY_NOW requires zero remaining gates"):
        normalize_candidate_decision(
            _v08_candidate(
                "BUY_NOW",
                current_price="102.00",
                distance_to_trigger_pct="-2.00",
                technical_state="confirmed",
                remaining_gate_count=1,
            ),
            contract_version="0.8",
        )


@pytest.mark.parametrize(
    "failed_gate",
    ["technical_gate_passed", "market_regime_gate_passed"],
)
def test_v08_almost_ready_rejects_multiple_represented_failures(
    failed_gate,
) -> None:
    with pytest.raises(ValueError, match="failed represented gate"):
        normalize_candidate_decision(
            _v08_candidate(**{failed_gate: False}),
            contract_version="0.8",
        )


def test_v08_almost_ready_accepts_one_non_price_gate_at_trigger() -> None:
    decision = normalize_candidate_decision(
        _v08_candidate(
            current_price="100.00",
            distance_to_trigger_pct="0.00",
            technical_gate_passed=False,
            technical_state="near_trigger",
        ),
        contract_version="0.8",
    )

    assert decision["remaining_gate_count"] == 1
    assert decision["technical_gate_passed"] is False


def test_v08_radar_cannot_understate_represented_failures() -> None:
    with pytest.raises(ValueError, match="cannot be less than the 2 failed"):
        normalize_candidate_decision(
            _v08_candidate(
                "RADAR",
                current_price="90.00",
                distance_to_trigger_pct="10.00",
                technical_gate_passed=False,
                remaining_gate_count=1,
            ),
            contract_version="0.8",
        )


def test_v08_radar_requires_a_remaining_gate() -> None:
    with pytest.raises(ValueError, match="RADAR requires at least one"):
        normalize_candidate_decision(
            _v08_candidate(
                "RADAR",
                current_price="102.00",
                distance_to_trigger_pct="-2.00",
                remaining_gate_count=0,
            ),
            contract_version="0.8",
        )


@pytest.mark.parametrize(
    ("technical_state", "technical_gate_passed", "message"),
    [
        ("confirmed", False, "confirmed requires technical_gate_passed=true"),
        ("no_setup", True, "no_setup requires technical_gate_passed=false"),
        ("invalidated", True, "invalidated requires technical_gate_passed=false"),
    ],
)
def test_v08_rejects_technical_state_gate_contradictions(
    technical_state, technical_gate_passed, message
) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_candidate_decision(
            _v08_candidate(
                technical_state=technical_state,
                technical_gate_passed=technical_gate_passed,
            ),
            contract_version="0.8",
        )


def test_v08_not_eligible_allows_unrepresented_hard_screen_failure() -> None:
    decision = normalize_candidate_decision(
        _v08_candidate(
            "NOT_ELIGIBLE",
            screen_bucket="rejected",
            technical_state="confirmed",
            current_price="100.00",
            trigger_price=None,
            distance_to_trigger_pct=None,
            invalidation_price=None,
            remaining_gate_count=0,
            status_reason="Revenue growth failed the hard screen.",
            buy_conditions=["Pass the required fundamental screen."],
        ),
        contract_version="0.8",
    )

    assert decision["buyability_status"] == "NOT_ELIGIBLE"


def test_v08_not_eligible_cannot_understate_represented_gate_failures() -> None:
    with pytest.raises(ValueError, match="cannot be less than the 1 failed"):
        normalize_candidate_decision(
            _v08_candidate(
                "NOT_ELIGIBLE",
                screen_bucket="rejected",
                technical_state="no_setup",
                technical_gate_passed=False,
                trigger_price=None,
                distance_to_trigger_pct=None,
                invalidation_price=None,
                remaining_gate_count=0,
            ),
            contract_version="0.8",
        )
