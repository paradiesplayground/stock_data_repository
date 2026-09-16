from types import SimpleNamespace

import pytest

from app.services.daily_stock_alert import _align_production_skill_metadata
from app.services.strategy_tracking import record_strategy_run


def _configuration(skill_version: str, *, growth_threshold=40) -> dict:
    return {
        "strategy": {
            "key": "dynamic_swing_buy_alerts",
            "version": "0.8",
            "name": "Dynamic swing buy alerts",
            "skill_version": skill_version,
        },
        "hard_thresholds": {"minimum_ttm_revenue_growth_pct": growth_threshold},
    }


class _DefinitionSession:
    def __init__(self, configuration: dict) -> None:
        self.definition = SimpleNamespace(
            configuration=configuration,
            skill_fingerprint=None,
        )

    def scalar(self, _statement):
        return self.definition


def _validate(session: _DefinitionSession, configuration: dict) -> dict:
    return record_strategy_run(
        session,
        strategy_key="dynamic_swing_buy_alerts",
        strategy_version="0.8",
        as_of_date="2026-09-15",
        run_type="as_run",
        idempotency_key="dynamic_swing_buy_alerts:0.8:2026-09-15:test",
        configuration=configuration,
        filters={},
        candidates=[],
        summary={},
        evidence=[],
        decision_contract_version="0.8",
        publish=False,
        validate_only=True,
    )


def test_v08_skill_version_metadata_aligns_to_registered_definition() -> None:
    registered = _configuration("1.5.3")
    submitted = _configuration("1.6.0")
    payload = {
        "strategy_key": "dynamic_swing_buy_alerts",
        "strategy_version": "0.8",
        "configuration": submitted,
    }
    session = _DefinitionSession(registered)

    _align_production_skill_metadata(session, payload)

    assert payload["configuration"]["strategy"]["skill_version"] == "1.5.3"
    assert _validate(session, payload["configuration"])["status"] == "valid"


def test_v08_skill_version_alignment_keeps_numeric_equivalence() -> None:
    registered = _configuration("1.5.3", growth_threshold=40)
    submitted = _configuration("1.6.0", growth_threshold=40.0)
    payload = {
        "strategy_key": "dynamic_swing_buy_alerts",
        "strategy_version": "0.8",
        "configuration": submitted,
    }
    session = _DefinitionSession(registered)

    _align_production_skill_metadata(session, payload)

    assert payload["configuration"]["strategy"]["skill_version"] == "1.5.3"
    assert _validate(session, payload["configuration"])["status"] == "valid"


def test_v08_real_configuration_change_still_requires_new_strategy_version() -> None:
    registered = _configuration("1.5.3", growth_threshold=40)
    submitted = _configuration("1.6.0", growth_threshold=41)
    payload = {
        "strategy_key": "dynamic_swing_buy_alerts",
        "strategy_version": "0.8",
        "configuration": submitted,
    }
    session = _DefinitionSession(registered)

    _align_production_skill_metadata(session, payload)

    assert payload["configuration"]["strategy"]["skill_version"] == "1.6.0"
    with pytest.raises(ValueError, match="strategy configuration changed"):
        _validate(session, payload["configuration"])
