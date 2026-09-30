from decimal import Decimal

from app.services.candidate_lifecycle import apply_active_trigger, transition_state
from app.services.daily_stock_alert_candidate_contract import normalize_candidate_state


def _candidate(*, status="RADAR", distance="8", decline="-18", risk=None):
    return {
        "ticker": "KLIC",
        "buyability_status": status,
        "distance_to_trigger_pct": distance,
        "trigger_price": "91.94",
        "current_price": "88",
        "metrics": {"price_change_12w_pct": decline},
        "deterministic_risk_flags": risk or [],
        "payload": {},
    }


def test_lifecycle_transitions_recovery_and_near_trigger_without_rejecting_exit():
    assert transition_state(_candidate(decline="-11.5", distance="14"), "FALLEN") == "RECOVERING"
    assert transition_state(_candidate(distance="4"), "RECOVERING") == "NEAR_TRIGGER"
    assert transition_state(_candidate(status="BUY_NOW", distance="0"), "NEAR_TRIGGER") == "ACTIONABLE"


def test_hard_deterministic_risk_invalidates_tracked_candidate():
    assert transition_state(_candidate(risk=["share_count_growth_at_or_above_15_pct"]), "RECOVERING") == "INVALIDATED"


def test_active_trigger_stays_fixed_while_rolling_reference_changes():
    lifecycle = type("Lifecycle", (), {"active_trigger": Decimal("91.94"), "active_trigger_set_date": None, "lifecycle_state": "RECOVERING"})()
    candidate = _candidate(distance="-2")
    candidate["suggested_trigger_price"] = "95.10"

    apply_active_trigger(candidate, lifecycle)

    assert candidate["trigger_price"] == "91.94"
    assert candidate["distance_to_trigger_pct"] == str(((Decimal("91.94") - Decimal("88")) / Decimal("91.94")) * 100)
    assert candidate["payload"]["rolling_trigger"] == "95.10"
    assert candidate["payload"]["active_trigger"] == "91.94"


def test_preparation_shape_uses_deterministic_metrics_for_price():
    lifecycle = type("Lifecycle", (), {"active_trigger": Decimal("91.94"), "active_trigger_set_date": None, "lifecycle_state": "RECOVERING"})()
    candidate = {
        "suggested_trigger_price": "95.10",
        "deterministic_metrics": {"close": "93.72"},
        "payload": {},
    }

    apply_active_trigger(candidate, lifecycle)

    assert candidate["trigger_price"] == "91.94"
    assert candidate["distance_to_trigger_pct"] == str(((Decimal("91.94") - Decimal("93.72")) / Decimal("91.94")) * 100)


def test_crossed_active_trigger_does_not_reanchor_when_confirmation_is_missing():
    lifecycle = type("Lifecycle", (), {"active_trigger": Decimal("91.94"), "active_trigger_set_date": None, "lifecycle_state": "NEAR_TRIGGER"})()
    candidate = _candidate(distance="-1", status="RADAR")
    candidate["suggested_trigger_price"] = "95.10"

    apply_active_trigger(candidate, lifecycle)

    assert candidate["trigger_price"] == "91.94"
    assert candidate["payload"]["active_trigger"] == "91.94"


def test_invalidated_is_terminal_without_an_explicit_reset():
    assert transition_state(_candidate(status="RADAR", distance="2"), "INVALIDATED") == "INVALIDATED"


def test_canonical_finalization_preserves_active_trigger_and_recalculates_gates():
    candidate = {
        "ticker": "KLIC",
        "screen_bucket": "qualified",
        "buyability_status": "RADAR",
        "current_price": "93.00",
        "trigger_price": "95.10",
        "distance_to_trigger_pct": "2.2",
        "technical_gate_passed": True,
        "market_regime_gate_passed": True,
        "remaining_gate_count": 0,
        "invalidation_price": "88",
        "metrics": {"close": "93.00"},
        "payload": {"active_trigger": "91.94", "lifecycle": {"active_trigger": "91.94"}},
        "buy_conditions": ["confirm volume"],
    }

    normalized = normalize_candidate_state(
        candidate,
        market_regime_gate_passed=True,
        fresh_checkpoint=None,
    )

    assert normalized["trigger_price"] == "91.94"
    assert normalized["distance_to_trigger_pct"] == str(((Decimal("91.94") - Decimal("93.00")) / Decimal("91.94")) * 100)
    assert normalized["technical_gate_passed"] is True
    assert normalized["payload"]["active_trigger"] == "91.94"
