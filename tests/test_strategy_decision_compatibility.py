from app.services.strategy_tracking import _canonical_hash, record_strategy_run


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
