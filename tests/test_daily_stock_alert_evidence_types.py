from types import SimpleNamespace

from app.config import Settings
from app.services import daily_stock_alert_workflow as workflow


class _Session:
    def __init__(self) -> None:
        self.added = None
        self.commits = 0

    def scalar(self, _statement):
        return None

    def add(self, item) -> None:
        self.added = item

    def commit(self) -> None:
        self.commits += 1


def _candidate_snapshot() -> dict:
    return {
        "ticker": "TEST",
        "deterministic_metrics": {"ticker": "TEST", "close": "95"},
        "suggested_trigger_price": "100",
        "suggested_invalidation_price": "90",
        "distance_to_trigger_pct": "5",
        "represented_gates": {
            "market_regime_gate_passed": True,
            "price_at_or_above_trigger": False,
        },
        "deterministic_risk_flags": [],
    }


def _snapshot() -> dict:
    return {
        "as_of_date": "2026-09-15",
        "deep_research_queue": [
            {"ticker": "TEST", "qualitative_evidence_required": ["dimension"]}
        ],
        "carry_forward_queue": [],
        "all_research_plans": [
            {
                "ticker": "TEST",
                "reasons": [],
                "evidence_state": "missing",
            }
        ],
        "candidate_snapshots": {"TEST": _candidate_snapshot()},
        "report_scope": {
            "detailed_tickers": ["TEST"],
            "compact_summary_tickers": [],
        },
        "run_template": {
            "summary": {
                "preparation_scope": {"expected_candidate_tickers": ["TEST"]}
            }
        },
    }


def test_evidence_type_normalizes_invalid_characters() -> None:
    value = workflow._normalize_evidence_type("SEC 10-Q / Going Concern Review!")

    assert value == "sec_10-q_going_concern_review"


def test_evidence_type_is_capped_at_64_characters() -> None:
    value = workflow._normalize_evidence_type("A" * 100)

    assert value == "a" * 64
    assert len(value) == 64


def test_research_checkpoint_normalizes_evidence_and_preserves_original(monkeypatch) -> None:
    preparation = SimpleNamespace(snapshot=_snapshot())
    monkeypatch.setattr(workflow, "_preparation", lambda *_args, **_kwargs: preparation)
    session = _Session()

    workflow.record_daily_stock_alert_research(
        session,
        preparation_id="prep-1",
        ticker="TEST",
        evidence=[
            {
                "ticker": "TEST",
                "evidence_type": "SEC 10-Q / Going Concern Review!",
                "details": {"source": "fresh research"},
            }
        ],
        required_dimensions={"dimension": True},
    )

    saved = session.added.evidence[0]
    assert saved["evidence_type"] == "sec_10-q_going_concern_review"
    assert saved["details"]["original_evidence_type"] == "SEC 10-Q / Going Concern Review!"
    assert saved["details"]["source"] == "fresh research"


def test_finalizer_normalizes_already_saved_invalid_evidence(monkeypatch) -> None:
    preparation = SimpleNamespace(
        preparation_id="prep-existing",
        snapshot=_snapshot(),
        final_payload=None,
        validation=None,
        production_run_id=None,
    )
    research = {
        "TEST": SimpleNamespace(
            ticker="TEST",
            evidence=[
                {
                    "ticker": "TEST",
                    "evidence_type": "This Existing Saved Evidence Type Has Spaces / Punctuation And Is Far Too Long For The Contract",
                    "details": {"checkpoint": "already_saved"},
                }
            ],
            qualitative_blockers=[],
            qualitative_flags=[],
            candidate_decision=None,
        )
    }
    monkeypatch.setattr(workflow, "_preparation", lambda *_args, **_kwargs: preparation)
    monkeypatch.setattr(workflow, "_research_by_ticker", lambda *_args, **_kwargs: research)

    def _validate(_session, _settings, **kwargs):
        evidence = kwargs["run_payload"]["evidence"][0]
        assert len(evidence["evidence_type"]) <= 64
        assert evidence["evidence_type"] == workflow._normalize_evidence_type(
            evidence["evidence_type"]
        )
        assert evidence["details"]["checkpoint"] == "already_saved"
        assert evidence["details"]["original_evidence_type"].startswith(
            "This Existing Saved Evidence Type"
        )
        return {
            "status": "valid",
            "payload_hash": "hash",
            "validated_run_payload": kwargs["run_payload"],
        }

    monkeypatch.setattr(workflow, "validate_daily_stock_alert", _validate)
    result = workflow.finalize_daily_stock_alert_preparation(
        _Session(), Settings(), preparation_id="prep-existing"
    )

    assert result["validated"]["status"] == "valid"
    assert result["run_payload"]["evidence"][0]["details"]["checkpoint"] == "already_saved"
