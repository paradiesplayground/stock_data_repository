from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services import daily_stock_alert_workflow as workflow


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
    return {ticker: SimpleNamespace(ticker=ticker, evidence=[{"ticker": ticker, "evidence_type": "review"}], qualitative_blockers=[], qualitative_flags=[], candidate_decision=None) for ticker in tickers}


def test_status_and_finalization_resume_after_one_of_five_research_checkpoints(monkeypatch) -> None:
    preparation = SimpleNamespace(preparation_id="prep-1", snapshot=_snapshot(), final_payload=None, validation=None, production_run_id=None)
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: _research(["T00"]))

    status = workflow.daily_stock_alert_preparation_status(_Session(), preparation_id="prep-1")
    assert status["completed_deep_research_tickers"] == ["T00"]
    assert status["outstanding_deep_research_tickers"] == ["T01", "T02", "T03", "T04"]
    with pytest.raises(ValueError, match="outstanding deep research"):
        workflow.finalize_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-1")


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


def test_validation_failure_does_not_save_final_payload_or_produce(monkeypatch) -> None:
    preparation = SimpleNamespace(preparation_id="prep-3", snapshot=_snapshot(1), final_payload=None, validation=None, production_run_id=None)
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args: _research(["T00"]))
    monkeypatch.setattr(workflow, "validate_daily_stock_alert", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("validation failed")))

    with pytest.raises(ValueError, match="validation failed"):
        workflow.finalize_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-3")
    assert preparation.final_payload is None


def test_repeated_production_uses_existing_idempotent_production_path(monkeypatch) -> None:
    preparation = SimpleNamespace(preparation_id="prep-4", snapshot=_snapshot(1), final_payload={"payload": "validated"}, validation={"status": "valid"}, production_run_id=None)
    calls = []
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    monkeypatch.setattr(workflow, "run_daily_stock_alert", lambda _session, _settings, **kwargs: calls.append(kwargs) or {"run_id": "run-1", "status": "completed", "idempotent_replay": len(calls) > 1})

    first = workflow.run_finalized_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-4")
    second = workflow.run_finalized_daily_stock_alert_preparation(_Session(), Settings(), preparation_id="prep-4")

    assert first["run_id"] == second["run_id"] == "run-1"
    assert all(call["verify_mailbox"] is False for call in calls)
