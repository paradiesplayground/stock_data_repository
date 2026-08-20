from app.services.strategy_tracking import _canonical_hash, record_strategy_run
import pytest


def test_legacy_candidate_payload_hash_remains_unchanged() -> None:
    expected_payload = {
        "strategy_key": "fallen-growth-swing",
        "strategy_version": "1.3.1",
        "as_of_date": "2026-08-14",
        "run_type": "replay",
        "configuration": {},
        "filters": {},
        "summary": None,
        "report_markdown": None,
        "candidates": [
            {
                "ticker": "AAPL",
                "stage": "watch",
                "action": "watch",
                "score": "75",
                "score_components": None,
                "metrics": None,
                "reasons": None,
                "trade_plan": None,
                "payload": None,
            }
        ],
        "evidence": [],
        "feature_calculation_version": None,
        "data_cutoff_at_utc": None,
        "skill_fingerprint": None,
    }
    expected_hash = _canonical_hash(expected_payload)
    existing = type(
        "Run",
        (),
        {
            "run_id": "legacy-run",
            "run_type": "replay",
            "payload_hash": expected_hash,
        },
    )()

    class ExistingSession:
        def scalar(self, _statement):
            return existing

    result = record_strategy_run(
        ExistingSession(),
        strategy_key="fallen-growth-swing",
        strategy_version="1.3.1",
        as_of_date="2026-08-14",
        run_type="replay",
        idempotency_key="legacy-run-key",
        configuration={},
        filters={},
        candidates=[
            {
                "ticker": "AAPL",
                "stage": "watch",
                "action": "watch",
                "score": 75,
            }
        ],
    )

    assert result["recorded"] is False
    assert result["idempotent_replay"] is True
    assert result["payload_hash"] == expected_hash


def test_new_as_run_cannot_silently_use_legacy_candidates() -> None:
    with pytest.raises(ValueError, match="decision_contract_version=0.7"):
        record_strategy_run(
            object(),
            strategy_key="dynamic_swing_buy_alerts",
            strategy_version="0.6",
            as_of_date="2026-08-20",
            run_type="as_run",
            idempotency_key="dynamic_swing_buy_alerts-0.6-2026-08-20",
            configuration={},
            filters={},
            candidates=[{"ticker": "AAPL", "stage": "qualified"}],
        )
