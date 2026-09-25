from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.services import daily_stock_alert_workflow as workflow
from app.services.daily_stock_alert_candidate_contract import validate_finalized_candidate_state


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def _candidate(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "deterministic_metrics": {
            "ticker": ticker, "close": "95", "relative_return_20d_vs_qqq_pct": "2",
            "relative_volume_20d": "1", "high_20d": "100", "low_20d": "88",
        },
        "suggested_trigger_price": "100.1", "suggested_invalidation_price": "92",
        "distance_to_trigger_pct": "5.1",
        "represented_gates": {"market_regime_gate_passed": True, "price_at_or_above_trigger": False, "price_within_five_pct_below_trigger": False, "relative_strength_gate_passed": True},
        "deterministic_risk_flags": [],
    }


def _snapshot(count: int = 5) -> dict:
    tickers = [f"T{index:02d}" for index in range(count)]
    plans = [{"ticker": ticker, "research_scope": "deep_research", "reasons": [], "evidence_state": "missing"} for ticker in tickers]
    return {
        "as_of_date": "2026-09-15",
        "deep_research_queue": [{"ticker": ticker, "qualitative_evidence_required": ["dimension"]} for ticker in tickers],
        "carry_forward_queue": [], "all_research_plans": plans,
        "candidate_snapshots": {ticker: _candidate(ticker) for ticker in tickers},
        "report_scope": {"detailed_tickers": tickers[:20], "compact_summary_tickers": tickers[20:]},
        "run_template": {"as_of_date": "2026-09-15", "candidates": [], "evidence": [], "summary": {"preparation_scope": {"expected_candidate_tickers": tickers, "research_scope_by_ticker": {ticker: {"evidence_state": "missing", "requires_current_qualitative_confirmation_for_actionable_status": True} for ticker in tickers}}}},
    }


def _research(tickers: list[str]) -> dict:
    reviewed_at = datetime(2026, 9, 15, 15, 1, tzinfo=timezone.utc)
    return {ticker: SimpleNamespace(ticker=ticker, evidence=[{"ticker": ticker, "evidence_type": "review"}], qualitative_blockers=[], qualitative_flags=[], candidate_decision=None, updated_at_utc=reviewed_at) for ticker in tickers}


def test_status_and_finalization_resume_after_one_of_five_research_checkpoints(monkeypatch) -> None:
    preparation = SimpleNamespace(preparation_id="prep-1", snapshot=_snapshot(), final_payload=None, validation=None, production_run_id=None)
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: _research(["T00"]))

    status = workflow.daily_stock_alert_preparation_status(_Session(), preparation_id="prep-1")
    assert status["completed_deep_research_tickers"] == ["T00"]
    assert status["outstanding_deep_research_tickers"] == ["T01", "T02", "T03", "T04"]
    with pytest.raises(ValueError, match="outstanding deep research"):
        workflow.finalize_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-1")


def test_final_research_checkpoint_automatically_starts_server_advancement(monkeypatch) -> None:
    preparation = SimpleNamespace(preparation_id="prep-auto", snapshot=_snapshot(1))
    calls = []

    class SessionWithResearchWrite:
        def scalar(self, _statement):
            return None

        def add(self, _item):
            pass

        def commit(self):
            pass

        def scalars(self, _statement):
            raise AssertionError("advancement is mocked at the worker boundary")

    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(
        workflow,
        "advance_daily_stock_alert_preparation",
        lambda _session, settings, *, preparation_id: calls.append((settings, preparation_id)) or {"status": "pending"},
    )

    result = workflow.record_daily_stock_alert_research(
        SessionWithResearchWrite(), settings=Settings(), preparation_id="prep-auto", ticker="T00",
        evidence=[], required_dimensions={"dimension": True},
    )

    assert isinstance(calls[0][0], Settings)
    assert calls[0][1] == "prep-auto"
    assert result["advancement"] == {"status": "pending"}


def test_finalization_after_completed_checkpoint_builds_all_candidates_and_is_idempotent(monkeypatch) -> None:
    preparation = SimpleNamespace(preparation_id="prep-2", snapshot=_snapshot(48), final_payload=None, validation=None, production_run_id=None)
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: _research(list(preparation.snapshot["candidate_snapshots"])))
    monkeypatch.setattr(workflow, "validate_daily_stock_alert", lambda _session, _settings, **kwargs: {"status": "valid", "payload_hash": "hash", "validated_run_payload": kwargs["run_payload"]})

    first = workflow.finalize_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-2")
    second = workflow.finalize_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-2")

    assert len(first["run_payload"]["candidates"]) == 48
    assert second["idempotent_replay"] is True


def test_repeated_preparation_returns_existing_checkpoint() -> None:
    existing = SimpleNamespace(snapshot={"status": "prepared"}, preparation_id="prep-existing")
    session = SimpleNamespace(scalar=lambda _statement: existing, add=lambda _item: None, flush=lambda: None, commit=lambda: None)
    result = workflow.persist_preparation(session, {"run_template": {"idempotency_key": "key"}, "as_of_date": "2026-09-15", "strategy_key": "dynamic_swing_buy_alerts", "strategy_version": "0.8"})

    assert result == {"status": "prepared", "preparation_id": "prep-existing", "preparation_reused": True}


def test_deterministic_only_candidate_cannot_become_buy_without_saved_research() -> None:
    plan = {"reasons": [], "evidence_state": "missing"}
    candidate = workflow._default_candidate(_candidate("SAFE"), plan, None)

    assert candidate["buyability_status"] == "RADAR"
    assert candidate["payload"]["qualitative_evidence_status"] == "missing"
    assert candidate["payload"]["setup_buyability_status"] == "RADAR"
    assert candidate["status_reason"].startswith("Deterministic-only classification")
    assert candidate["payload"]["presentation"]["group"] == "watch_first"
    assert candidate["payload"]["presentation"]["blockers"]


def test_report_copy_does_not_violate_the_canonical_finalizer_contract() -> None:
    candidate = workflow._default_candidate(
        _candidate("AAOI"), {"reasons": [], "evidence_state": "missing"}, None
    )

    validate_finalized_candidate_state(
        candidate,
        preparation_id="prep-aaoi",
        preparation_created_at_utc=None,
        require_fresh_research_for_actionable=False,
    )


def test_finalizer_counts_positive_distance_as_a_represented_failed_gate() -> None:
    snapshot = _candidate("AAOI")
    snapshot["represented_gates"] = {
        **snapshot["represented_gates"],
        "price_at_or_above_trigger": False,
        "market_regime_gate_passed": False,
    }
    snapshot["distance_to_trigger_pct"] = "5.1"

    candidate = workflow._default_candidate(snapshot, {"reasons": [], "evidence_state": "missing"}, None)

    assert candidate["technical_gate_passed"] is False
    assert candidate["market_regime_gate_passed"] is False
    assert candidate["distance_to_trigger_pct"] == "5.1"
    assert candidate["remaining_gate_count"] >= 3


def test_block_regime_preserves_underlying_actionable_setup_before_effective_veto() -> None:
    snapshot = _candidate("APP")
    snapshot["represented_gates"] = {
        **snapshot["represented_gates"],
        "market_regime_gate_passed": False,
    }
    snapshot["distance_to_trigger_pct"] = "-1.0"
    research = SimpleNamespace(
        evidence=[], qualitative_blockers=[], qualitative_flags=[],
        candidate_decision={
            "buyability_status": "BUY_NOW",
            "screen_bucket": "qualified",
            "technical_state": "confirmed",
            "technical_gate_passed": True,
            "market_regime_gate_passed": True,
            "remaining_gate_count": 0,
            "status_reason": "The stock-specific setup is ready.",
            "buy_conditions": ["Keep price inside the planned entry zone."],
        },
    )

    candidate = workflow._default_candidate(
        snapshot, {"reasons": [], "evidence_state": "missing"}, research,
        {"checkpoint": "fresh"},
    )

    assert candidate["buyability_status"] == "NOT_ELIGIBLE"
    assert candidate["screen_bucket"] == "rejected"
    assert candidate["market_regime_gate_passed"] is False
    assert candidate["remaining_gate_count"] == 1
    assert candidate["payload"]["setup_buyability_status"] == "BUY_NOW"
    assert candidate["payload"]["setup_screen_bucket"] == "qualified"
    assert candidate["payload"]["setup_remaining_gate_count"] == 0
    assert candidate["payload"]["market_actionability_status"] == "MARKET_BLOCKED"


def test_block_regime_preserves_dropped_bucket_as_intrinsically_not_eligible() -> None:
    snapshot = _candidate("DROP")
    snapshot["represented_gates"] = {
        **snapshot["represented_gates"],
        "market_regime_gate_passed": False,
    }
    research = SimpleNamespace(
        evidence=[], qualitative_blockers=[], qualitative_flags=[],
        candidate_decision={"buyability_status": "ALMOST_READY", "screen_bucket": "dropped"},
    )

    candidate = workflow._default_candidate(
        snapshot,
        {"reasons": ["dropped_from_raw_pool"], "evidence_state": "missing"},
        research,
        {"checkpoint": "fresh"},
    )

    assert candidate["buyability_status"] == "NOT_ELIGIBLE"
    assert candidate["screen_bucket"] == "dropped"
    assert candidate["payload"]["setup_buyability_status"] == "NOT_ELIGIBLE"
    assert candidate["payload"]["setup_screen_bucket"] == "dropped"


def test_non_block_regime_keeps_saved_decision_unchanged_and_records_setup_overlay() -> None:
    research = SimpleNamespace(
        evidence=[], qualitative_blockers=[], qualitative_flags=[],
        candidate_decision={"buyability_status": "BUY_NOW", "screen_bucket": "qualified"},
    )

    candidate = workflow._default_candidate(
        _candidate("OPEN"), {"reasons": [], "evidence_state": "missing"}, research,
        {"checkpoint": "fresh"},
    )

    assert candidate["buyability_status"] == "BUY_NOW"
    assert candidate["screen_bucket"] == "qualified"
    assert candidate["payload"]["setup_buyability_status"] == "BUY_NOW"
    assert candidate["payload"]["market_actionability_status"] == "MARKET_OPEN"


def test_not_eligible_saved_setup_maps_qualified_bucket_to_effective_rejected() -> None:
    """KRMN-shaped resumed research must satisfy the v0.8 terminal bucket rule."""
    research = SimpleNamespace(
        evidence=[], qualitative_blockers=[], qualitative_flags=[],
        candidate_decision={
            "buyability_status": "NOT_ELIGIBLE",
            "screen_bucket": "qualified",
            "status_reason": "Saved research rejects the setup.",
            "buy_conditions": ["Wait for a valid setup."],
            "remaining_gate_count": 1,
        },
    )

    candidate = workflow._default_candidate(
        _candidate("KRMN"), {"reasons": [], "evidence_state": "missing"}, research,
        {"checkpoint": "fresh"},
    )

    assert candidate["buyability_status"] == "NOT_ELIGIBLE"
    assert candidate["screen_bucket"] == "rejected"
    assert candidate["payload"]["setup_screen_bucket"] == "qualified"


def test_almost_ready_saved_setup_derives_its_one_canonical_remaining_gate() -> None:
    """MXL-shaped saved research cannot leak a stale remaining-gate count."""
    snapshot = _candidate("MXL")
    snapshot.update(
        deterministic_metrics={
            **snapshot["deterministic_metrics"], "close": "99",
        },
        suggested_trigger_price="100",
        suggested_invalidation_price="95",
        distance_to_trigger_pct="1",
        represented_gates={
            **snapshot["represented_gates"],
            "price_at_or_above_trigger": True,
            "market_regime_gate_passed": True,
        },
    )
    research = SimpleNamespace(
        evidence=[], qualitative_blockers=[], qualitative_flags=[],
        candidate_decision={
            "buyability_status": "ALMOST_READY",
            "screen_bucket": "qualified",
            "technical_state": "confirmed",
            "technical_gate_passed": True,
            "market_regime_gate_passed": True,
            "remaining_gate_count": 3,
            "status_reason": "Saved research confirms the near-trigger setup.",
            "buy_conditions": ["Wait for the remaining price gate."],
        },
    )

    candidate = workflow._default_candidate(
        snapshot, {"reasons": [], "evidence_state": "missing"}, research,
        {"checkpoint": "fresh"},
    )

    assert candidate["buyability_status"] == "ALMOST_READY"
    assert candidate["remaining_gate_count"] == 1
    assert candidate["trigger_price"] == "100"
    assert candidate["invalidation_price"] == "95"


def test_finalized_report_surfaces_market_blocked_setup_quality(monkeypatch) -> None:
    snapshot = _snapshot(1)
    candidate_snapshot = snapshot["candidate_snapshots"]["T00"]
    candidate_snapshot["represented_gates"] = {
        **candidate_snapshot["represented_gates"],
        "market_regime_gate_passed": False,
    }
    candidate_snapshot["distance_to_trigger_pct"] = "-1.0"
    preparation = SimpleNamespace(
        preparation_id="prep-blocked",
        snapshot=snapshot,
        final_payload=None,
        validation=None,
        production_run_id=None,
        created_at_utc=datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc),
    )
    research = {
        "T00": SimpleNamespace(
            ticker="T00",
            evidence=[{"ticker": "T00", "evidence_type": "review"}],
            qualitative_blockers=[],
            qualitative_flags=[],
            candidate_decision={
                "buyability_status": "BUY_NOW",
                "screen_bucket": "qualified",
                "technical_state": "confirmed",
                "technical_gate_passed": True,
                "market_regime_gate_passed": True,
                "remaining_gate_count": 0,
                "status_reason": "Stock-specific gates pass.",
                "buy_conditions": ["Hold the entry zone."],
            },
            updated_at_utc=datetime(2026, 9, 15, 15, 1, tzinfo=timezone.utc),
        )
    }
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: research)
    monkeypatch.setattr(
        workflow,
        "validate_daily_stock_alert",
        lambda _session, _settings, **kwargs: {
            "status": "valid",
            "payload_hash": "hash",
            "validated_run_payload": kwargs["run_payload"],
        },
    )

    result = workflow.finalize_daily_stock_alert_preparation(
        _Session(), Settings(), preparation_id="prep-blocked"
    )

    assert "BUY_NOW setup (MARKET BLOCKED)" in result["run_payload"]["report_markdown"]
    assert result["run_payload"]["summary"]["setup_counts"]["BUY_NOW"] == 1
    assert result["run_payload"]["summary"]["market_blocked_count"] == 1
    assert result["run_payload"]["summary"]["candidate_counts"]["NOT_ELIGIBLE"] == 1


def test_report_uses_one_watch_first_policy_and_keeps_alab_screen_failure_visible(monkeypatch) -> None:
    """Regression for run 242477b3: a trigger cannot erase ALAB's screen exit."""
    snapshot = _snapshot(2)
    snapshot["candidate_snapshots"]["T00"]["deterministic_metrics"].update(
        revenue_ttm_yoy_pct="75", latest_quarter_revenue_yoy_pct="80",
        price_change_12w_pct="-25", avg_dollar_volume_20d="100000000",
    )
    alab = snapshot["candidate_snapshots"].pop("T01")
    alab["ticker"] = "ALAB"
    alab["deterministic_metrics"].update(
        ticker="ALAB", revenue_ttm_yoy_pct="75", latest_quarter_revenue_yoy_pct="80",
        price_change_12w_pct="-16.3394", avg_dollar_volume_20d="100000000",
    )
    snapshot["candidate_snapshots"]["ALAB"] = alab
    snapshot["deep_research_queue"][1]["ticker"] = "ALAB"
    snapshot["all_research_plans"][1].update(ticker="ALAB", reasons=["dropped_from_raw_pool"])
    scope = snapshot["run_template"]["summary"]["preparation_scope"]
    scope["expected_candidate_tickers"] = ["T00", "ALAB"]
    scope["research_scope_by_ticker"]["ALAB"] = scope["research_scope_by_ticker"].pop("T01")
    snapshot["report_scope"] = {"detailed_tickers": ["T00", "ALAB"], "compact_summary_tickers": []}
    preparation = SimpleNamespace(
        preparation_id="242477b3-2eca-4653-9f12-6354747f8ee9",
        created_at_utc=datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc),
        snapshot=snapshot, final_payload=None, validation=None, production_run_id=None,
    )
    research = _research(["T00", "ALAB"])
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: research)
    monkeypatch.setattr(workflow, "validate_daily_stock_alert", lambda _session, _settings, **kwargs: {"status": "valid", "payload_hash": "hash", "validated_run_payload": kwargs["run_payload"]})

    result = workflow.finalize_daily_stock_alert_preparation(_Session(), Settings(), preparation_id=preparation.preparation_id)

    summary = result["run_payload"]["summary"]["presentation"]
    report = result["run_payload"]["report_markdown"]
    assert summary["watch_first_tickers"] == ["T00"]
    assert summary["excluded_worth_reviewing_tickers"] == ["ALAB"]
    assert "## Watch first\n### T00" in report
    assert "## Excluded — worth reviewing\n\n### ALAB" in report
    assert "12-week price change is -16.3%; required <= -20.0%." in report
    assert "12-week price change must be <= -20.0%." in report
    assert "$100.10" in report  # The trigger remains technical-only, not requalification.
    assert "Complete current qualitative confirmation" not in report


def test_resumed_production_fixture_rebuilds_the_same_canonical_blocked_candidate(monkeypatch) -> None:
    """A saved production checkpoint must not regain actionability on resume."""
    snapshot = _snapshot(1)
    snapshot["candidate_snapshots"]["T00"].update(
        distance_to_trigger_pct="-1.0",
        represented_gates={
            **snapshot["candidate_snapshots"]["T00"]["represented_gates"],
            "market_regime_gate_passed": False,
        },
    )
    preparation = SimpleNamespace(
        preparation_id="production-resume-2026-09-15",
        created_at_utc=datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc),
        snapshot=snapshot,
        final_payload=None,
        validation=None,
        production_run_id=None,
    )
    resumed_research = {
        "T00": SimpleNamespace(
            ticker="T00",
            updated_at_utc=datetime(2026, 9, 15, 15, 2, tzinfo=timezone.utc),
            evidence=[{"ticker": "T00", "evidence_type": "review"}],
            qualitative_blockers=[],
            qualitative_flags=[],
            candidate_decision={
                "buyability_status": "BUY_NOW",
                "screen_bucket": "qualified",
                "technical_state": "confirmed",
                "technical_gate_passed": True,
                "market_regime_gate_passed": True,
                "remaining_gate_count": 0,
                "status_reason": "Saved production research confirms the setup.",
                "buy_conditions": ["Hold the planned entry zone."],
            },
        )
    }
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: resumed_research)
    monkeypatch.setattr(
        workflow,
        "validate_daily_stock_alert",
        lambda _session, _settings, **kwargs: {
            "status": "valid", "payload_hash": "resumed-hash",
            "validated_run_payload": kwargs["run_payload"],
        },
    )

    result = workflow.finalize_daily_stock_alert_preparation(
        _Session(), Settings(), preparation_id=preparation.preparation_id
    )

    candidate = result["run_payload"]["candidates"][0]
    assert candidate["buyability_status"] == "NOT_ELIGIBLE"
    assert candidate["screen_bucket"] == "rejected"
    assert candidate["remaining_gate_count"] == 1
    assert candidate["payload"]["setup_buyability_status"] == "BUY_NOW"
    assert candidate["payload"]["qualitative_research_checkpoint"] == {
        "preparation_id": preparation.preparation_id,
        "ticker": "T00",
        "research_scope": "deep_research",
        "completed_at_utc": "2026-09-15T15:02:00+00:00",
    }


def test_validation_failure_does_not_save_final_payload_or_produce(monkeypatch) -> None:
    preparation = SimpleNamespace(preparation_id="prep-3", snapshot=_snapshot(1), final_payload=None, validation=None, production_run_id=None)
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: _research(["T00"]))
    monkeypatch.setattr(workflow, "validate_daily_stock_alert", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("validation failed")))

    with pytest.raises(ValueError, match="validation failed"):
        workflow.finalize_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-3")
    assert preparation.final_payload is None


def test_repeated_production_uses_existing_idempotent_production_path(monkeypatch) -> None:
    preparation = SimpleNamespace(
        preparation_id="prep-4", snapshot=_snapshot(1), final_payload={"payload": "validated"},
        validation={"status": "valid"}, production_run_id=None, website_status="pending",
        email_status="pending", stage="canonical_run", status="pending", last_error=None,
    )
    calls = []
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: _research(["T00"]))
    monkeypatch.setattr(workflow, "record_strategy_run", lambda *_args, **kwargs: calls.append(kwargs) or {"run_id": "run-1", "payload_hash": "hash"})
    monkeypatch.setattr(workflow, "get_strategy_run", lambda *_args: {"found": True, "payload_hash": "hash"})
    monkeypatch.setattr(workflow, "publish_strategy_run_only", lambda *_args: {"status": "published"})
    monkeypatch.setattr(workflow, "send_strategy_run_email", lambda *_args: {"status": "sent"})

    first = workflow.run_finalized_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-4")
    second = workflow.run_finalized_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-4")

    assert preparation.production_run_id == "run-1"
    assert preparation.website_status == preparation.email_status == "complete"
    assert len(calls) == 1
    assert first["production_status"] == second["production_status"] == "completed"


@pytest.mark.parametrize("failure_stage", ["canonical_run", "website", "email"])
def test_delivery_transition_failure_preserves_the_canonical_run_and_retries_only_that_stage(monkeypatch, failure_stage: str) -> None:
    preparation = SimpleNamespace(
        preparation_id=f"failure-{failure_stage}", snapshot=_snapshot(1), final_payload={"payload": "validated"},
        validation={"status": "valid"}, production_run_id="run-1" if failure_stage != "canonical_run" else None,
        canonical_run_status="complete" if failure_stage != "canonical_run" else "pending",
        website_status="complete" if failure_stage == "email" else "pending", email_status="pending",
        stage=failure_stage, status="pending", last_error=None,
    )
    calls: list[str] = []
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: _research(["T00"]))
    def persist(*_args, **_kwargs):
        calls.append("persist")
        if failure_stage == "canonical_run" and calls.count("persist") == 1:
            raise RuntimeError("database down")
        return {"run_id": "run-1", "payload_hash": "hash"}
    monkeypatch.setattr(workflow, "record_strategy_run", persist)
    monkeypatch.setattr(workflow, "get_strategy_run", lambda *_args: {"found": True, "payload_hash": "hash"})
    def website(*_args):
        calls.append("website")
        if failure_stage == "website" and calls.count("website") == 1:
            raise RuntimeError("website down")
        return {"status": "published"}
    def email(*_args):
        calls.append("email")
        if failure_stage == "email" and calls.count("email") == 1:
            raise RuntimeError("smtp down")
        return {"status": "sent"}
    monkeypatch.setattr(workflow, "publish_strategy_run_only", website)
    monkeypatch.setattr(workflow, "send_strategy_run_email", email)

    first = workflow.advance_daily_stock_alert_preparation(_Session(), Settings(), preparation_id=preparation.preparation_id)
    second = workflow.advance_daily_stock_alert_preparation(_Session(), Settings(), preparation_id=preparation.preparation_id)

    assert first["status"] == "failed"
    assert second["production_status"] == "completed"
    assert calls.count("persist") == (2 if failure_stage == "canonical_run" else 0)
    expected_website_attempts = 2 if failure_stage == "website" else (0 if failure_stage == "email" else 1)
    assert calls.count("website") == expected_website_attempts
    assert calls.count("email") == (2 if failure_stage == "email" else 1)
