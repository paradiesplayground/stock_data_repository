from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.candidate_lifecycle import apply_active_trigger, archive_stale_lifecycles, transition_state
from app.services.daily_stock_alert_candidate_contract import normalize_candidate_state
from app.services import strategy_tracking


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


def test_stale_lifecycle_is_archived_after_trading_session_timeout(monkeypatch):
    row = SimpleNamespace(
        ticker="KLIC",
        strategy_key="dynamic_swing_buy_alerts",
        lifecycle_state="RECOVERING",
        lifecycle_state_since=None,
        last_discovery_screen_date="2026-08-01",
        last_material_event=None,
        last_material_event_date=None,
        archive_reason=None,
        archived_date=None,
        active_trigger=Decimal("91.94"),
    )
    session = SimpleNamespace(
        scalars=lambda statement: SimpleNamespace(all=lambda: [row]),
    )
    monkeypatch.setattr(
        "app.services.candidate_lifecycle.trading_sessions_since",
        lambda *args, **kwargs: 30,
    )

    archived = archive_stale_lifecycles(
        session,
        strategy_key="dynamic_swing_buy_alerts",
        as_of_date="2026-09-15",
        observed_tickers=set(),
        timeout_trading_sessions=30,
    )

    assert archived == ["KLIC"]
    assert row.lifecycle_state == "INVALIDATED"
    assert row.last_material_event == "STALE_TIMEOUT"
    assert row.archive_reason == "candidate absent from discovery for 30 trading sessions"
    assert row.active_trigger is None


def test_stale_lifecycle_does_not_archive_before_timeout(monkeypatch):
    row = SimpleNamespace(
        ticker="KLIC", lifecycle_state="RECOVERING", last_discovery_screen_date="2026-08-01",
    )
    session = SimpleNamespace(scalars=lambda statement: SimpleNamespace(all=lambda: [row]))
    monkeypatch.setattr("app.services.candidate_lifecycle.trading_sessions_since", lambda *args, **kwargs: 29)

    assert archive_stale_lifecycles(
        session, strategy_key="dynamic_swing_buy_alerts", as_of_date="2026-09-15",
        observed_tickers=set(), timeout_trading_sessions=30,
    ) == []


def test_stale_lifecycle_bootstraps_timeout_from_first_discovered_date(monkeypatch):
    row = SimpleNamespace(
        ticker="KLIC",
        lifecycle_state="RECOVERING",
        first_discovered_date=date(2026, 8, 1),
        last_discovery_screen_date=None,
        lifecycle_state_since=None,
        last_material_event=None,
        last_material_event_date=None,
        archive_reason=None,
        archived_date=None,
        active_trigger=Decimal("91.94"),
    )
    session = SimpleNamespace(scalars=lambda statement: SimpleNamespace(all=lambda: [row]))
    anchors = []
    session_counts = iter((29, 30))

    def count_sessions(*args, **kwargs):
        anchors.append(kwargs["start_date"])
        return next(session_counts)

    monkeypatch.setattr("app.services.candidate_lifecycle.trading_sessions_since", count_sessions)

    assert archive_stale_lifecycles(
        session, strategy_key="dynamic_swing_buy_alerts", as_of_date=date(2026, 9, 15),
        observed_tickers=set(), timeout_trading_sessions=30,
    ) == []
    assert row.lifecycle_state == "RECOVERING"

    assert archive_stale_lifecycles(
        session, strategy_key="dynamic_swing_buy_alerts", as_of_date=date(2026, 9, 15),
        observed_tickers=set(), timeout_trading_sessions=30,
    ) == ["KLIC"]
    assert anchors == [date(2026, 8, 1), date(2026, 8, 1)]
    assert row.lifecycle_state == "INVALIDATED"
    assert row.last_material_event == "STALE_TIMEOUT"
    assert row.archived_date == date(2026, 9, 15)
    assert row.archive_reason == "candidate absent from discovery for 30 trading sessions"
    assert row.active_trigger is None


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


def test_canonical_finalization_drops_stale_rolling_trigger_gates_but_preserves_independent_gates():
    candidate = {
        "ticker": "KLIC",
        "screen_bucket": "qualified",
        "buyability_status": "RADAR",
        "current_price": "93.00",
        "trigger_price": "95.10",
        "distance_to_trigger_pct": "2.2",
        "technical_gate_passed": False,
        "market_regime_gate_passed": False,
        "remaining_gate_count": 4,
        "invalidation_price": "88",
        "metrics": {"close": "93.00"},
        "payload": {"active_trigger": "91.94", "lifecycle": {"active_trigger": "91.94"}},
    }

    normalized = normalize_candidate_state(
        candidate,
        market_regime_gate_passed=False,
        fresh_checkpoint=None,
    )

    assert normalized["trigger_price"] == "91.94"
    assert normalized["distance_to_trigger_pct"] == str(((Decimal("91.94") - Decimal("93.00")) / Decimal("91.94")) * 100)
    assert normalized["technical_gate_passed"] is True
    assert normalized["market_regime_gate_passed"] is False
    assert normalized["remaining_gate_count"] == 2


def test_canonical_run_persists_each_lifecycle_once(monkeypatch):
    calls = []
    row = SimpleNamespace(
        lifecycle_state="RECOVERING",
        lifecycle_state_since=SimpleNamespace(isoformat=lambda: "2026-09-29"),
        days_on_watch=1,
        active_trigger=Decimal("91.94"),
        active_trigger_set_date=None,
        rolling_trigger=Decimal("95.10"),
        current_trigger_distance_pct=Decimal("-1.15"),
        best_trigger_distance_pct=Decimal("-1.15"),
        last_material_event=None,
        archive_reason=None,
    )

    monkeypatch.setattr(strategy_tracking, "get_lifecycle", lambda *args: None)
    monkeypatch.setattr(
        strategy_tracking,
        "persist_lifecycle",
        lambda *args, **kwargs: calls.append(kwargs["candidate"]["ticker"]) or row,
    )

    strategy_tracking._persist_run_lifecycles(
        SimpleNamespace(),
        strategy_key="dynamic_swing_buy_alerts",
        as_of_date="2026-09-29",
        normalized_candidates=[
            {
                "ticker": "KLIC",
                "decision": {
                    "buyability_status": "RADAR",
                    "distance_to_trigger_pct": "-1.15",
                    "current_price": "93.00",
                    "trigger_price": "91.94",
                },
                "metrics": {},
                "payload": {},
            }
        ],
    )

    assert calls == ["KLIC"]
