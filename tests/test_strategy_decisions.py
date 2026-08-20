from decimal import Decimal

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
