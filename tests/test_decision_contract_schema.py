import json

from app.decision_contract import (
    CONTRACT_PATH,
    DECISION_CONTRACT_VERSION,
    DECISION_PRIORITY,
    DECISION_STATUSES,
    SCREEN_BUCKETS,
    TECHNICAL_STATES,
)


def test_canonical_decision_schema_drives_runtime_constants() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert DECISION_CONTRACT_VERSION == "0.7"
    assert DECISION_STATUSES == set(
        contract["properties"]["decision_status"]["enum"]
    )
    assert SCREEN_BUCKETS == set(contract["properties"]["screen_bucket"]["enum"])
    assert TECHNICAL_STATES == set(
        contract["properties"]["technical_state"]["enum"]
    )
    assert sorted(DECISION_PRIORITY.values()) == list(range(len(DECISION_STATUSES)))


def test_schema_requires_every_v07_structural_field() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert set(contract["required"]) == {
        "ticker",
        "screen_bucket",
        "technical_state",
        "decision_status",
        "status_reason",
        "next_condition",
        "metrics",
        "technical_gate_passed",
        "market_regime_gate_passed",
    }
    assert set(contract["properties"]["metrics"]["required"]) == {
        "close",
        "relative_return_20d_vs_qqq_pct",
        "relative_volume_20d",
    }
