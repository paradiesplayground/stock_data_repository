import copy
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import StrategyDefinition, StrategyRun
from app.services.daily_stock_alert import (
    _attach_company_names,
    run_daily_stock_alert,
    validate_daily_stock_alert,
)
from app.services.daily_stock_alert_preparation import prepare_daily_stock_alert


def _payload() -> dict:
    return {
        "strategy_key": "daily-alert",
        "strategy_version": "0.8",
        "as_of_date": "2026-08-21",
        "run_type": "as_run",
        "idempotency_key": "daily-alert:0.8:2026-08-21",
        "configuration": {},
        "filters": {},
        "candidates": [],
        "decision_contract_version": "0.8",
        "report_markdown": "# Daily alert\n\nNo trade today.",
    }


class _ContractSession:
    def __init__(self, configuration: dict) -> None:
        self.definition = SimpleNamespace(
            id=1,
            configuration=configuration,
            skill_fingerprint=None,
        )
        self.run = None

    def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is StrategyDefinition:
            return self.definition
        if entity is StrategyRun:
            return None
        raise AssertionError(f"unexpected scalar query for {entity}")

    def add(self, item) -> None:
        if isinstance(item, StrategyRun):
            self.run = item

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        return None


def _numbers_as_floats(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, list):
        return [_numbers_as_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _numbers_as_floats(item) for key, item in value.items()}
    return value


def _prepared_empty_alert(monkeypatch) -> dict:
    freshness = {
        "expected_market_date": "2026-08-28",
        "ready_for_screening": True,
        "checked_at_local": "2026-08-31T09:00:00-05:00",
        "latest_trade_date": "2026-08-28",
        "latest_feature_date": "2026-08-28",
        "freshness_issues": [],
    }
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.get_data_freshness",
        lambda _session, _settings: freshness,
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation.query_security_features",
        lambda _session, **_kwargs: {
            "as_of_date": "2026-08-28",
            "calculation_version": "1.5.0",
            "count": 0,
            "items": [],
            "excluded_industry_groups": [],
            "excluded_sic_prefixes": [],
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation._spy_market_regime",
        lambda _session, _date: {
            "benchmark_ticker": "SPY",
            "status": "pass",
            "gate_passed": True,
            "latest_close": "769.35",
            "sma_50": "753.96",
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert_preparation._prior_run",
        lambda _session, _date: None,
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert.get_data_freshness",
        lambda _session, _settings: freshness,
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert.build_daily_changes",
        lambda _session, *, payload: {"baseline": True},
    )
    prepared = prepare_daily_stock_alert(object(), Settings(), as_of_date="2026-08-28")
    payload = copy.deepcopy(prepared["run_template"])
    payload["summary"]["conclusion"] = "NO_TRADE_TODAY"
    payload["report_markdown"] = "# Daily alert\n\nNo trade today."
    return payload


def test_canonical_prepared_v08_payload_validates_then_runs_unchanged(
    monkeypatch,
) -> None:
    payload = _prepared_empty_alert(monkeypatch)
    original_configuration = copy.deepcopy(payload["configuration"])
    session = _ContractSession(_numbers_as_floats(original_configuration))
    monkeypatch.setattr(
        "app.services.daily_stock_alert.get_strategy_run",
        lambda _session, run_id: {
            "found": True,
            "run_id": run_id,
            "as_of_date": "2026-08-28",
            "payload_hash": session.run.payload_hash,
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert.publish_strategy_run",
        lambda _session, _settings, run_id: {
            "status": "published",
            "run_id": run_id,
        },
    )

    validated = validate_daily_stock_alert(
        session,
        Settings(),
        as_of_date="2026-08-28",
        run_payload=payload,
    )
    validated_payload = validated["validated_run_payload"]
    unchanged_payload = copy.deepcopy(validated_payload)
    result = run_daily_stock_alert(
        session,
        Settings(),
        as_of_date="2026-08-28",
        run_payload=validated_payload,
    )

    assert payload["configuration"] == original_configuration
    assert validated_payload == unchanged_payload
    assert result["status"] == "completed"
    assert result["recorded"] is True
    assert result["payload_hash"] == validated["payload_hash"]


@pytest.mark.parametrize(
    "operation", [validate_daily_stock_alert, run_daily_stock_alert]
)
def test_canonical_alert_rejects_genuinely_changed_v08_configuration(
    monkeypatch, operation
) -> None:
    payload = _prepared_empty_alert(monkeypatch)
    stored_configuration = copy.deepcopy(payload["configuration"])
    payload["configuration"]["universe"]["minimum_price"] = 6
    session = _ContractSession(stored_configuration)

    with pytest.raises(
        ValueError,
        match="strategy configuration changed; record it under a new strategy_version",
    ):
        operation(
            session,
            Settings(),
            as_of_date="2026-08-28",
            run_payload=payload,
        )


def test_company_names_are_attached_to_candidate_payloads() -> None:
    payload = {
        "summary": {
            "preparation_scope": {
                "company_names": {"IONQ": "IonQ, Inc."},
            }
        },
        "candidates": [
            {"ticker": "IONQ", "payload": {"in_raw_pool": True}},
            {"ticker": "UNKNOWN"},
        ],
    }

    _attach_company_names(payload)

    assert payload["candidates"][0]["payload"] == {
        "in_raw_pool": True,
        "company_name": "IonQ, Inc.",
    }
    assert payload["candidates"][1].get("payload") is None


def test_daily_alert_records_verifies_and_delivers_in_one_call(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(
        "app.services.daily_stock_alert.get_data_freshness",
        lambda _session, _settings: {
            "expected_market_date": "2026-08-21",
            "ready_for_screening": True,
            "checked_at_local": "2026-08-21T17:00:00-05:00",
            "latest_trade_date": "2026-08-21",
            "latest_feature_date": "2026-08-21",
            "freshness_issues": [],
        },
    )

    def record(_session, **payload):
        calls.append(("record", payload["publish"]))
        return {
            "run_id": "run-1",
            "recorded": True,
            "idempotent_replay": False,
            "payload_hash": "hash-1",
        }

    monkeypatch.setattr("app.services.daily_stock_alert.record_strategy_run", record)
    monkeypatch.setattr(
        "app.services.daily_stock_alert.get_strategy_run",
        lambda _session, _run_id: {
            "found": True,
            "as_of_date": "2026-08-21",
            "payload_hash": "hash-1",
        },
    )
    monkeypatch.setattr(
        "app.services.daily_stock_alert.publish_strategy_run",
        lambda _session, _settings, run_id: {
            "status": "published",
            "run_id": run_id,
            "email_delivery": "smtp_accepted",
        },
    )

    result = run_daily_stock_alert(
        object(),
        Settings(),
        as_of_date="2026-08-21",
        run_payload=_payload(),
    )

    assert calls == [("record", False)]
    assert result["status"] == "completed"
    assert result["run_id"] == "run-1"
    assert result["publication"]["email_delivery"] == "smtp_accepted"
    assert result["mailbox_verification"] == {"status": "not_requested"}


def test_daily_alert_dry_validation_uses_recording_contract_without_writes(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        "app.services.daily_stock_alert.get_data_freshness",
        lambda _session, _settings: {
            "expected_market_date": "2026-08-21",
            "ready_for_screening": True,
            "checked_at_local": "2026-08-21T17:00:00-05:00",
            "latest_trade_date": "2026-08-21",
            "latest_feature_date": "2026-08-21",
            "freshness_issues": [],
        },
    )

    def validate(_session, **payload):
        calls.append((payload["publish"], payload["validate_only"]))
        return {
            "status": "valid",
            "payload_hash": "hash-1",
            "candidate_count": 0,
            "evidence_count": 0,
            "persisted": False,
            "published": False,
            "emailed": False,
        }

    monkeypatch.setattr("app.services.daily_stock_alert.record_strategy_run", validate)
    result = validate_daily_stock_alert(
        object(), Settings(), as_of_date="2026-08-21", run_payload=_payload()
    )

    assert calls == [(False, True)]
    assert result["status"] == "valid"
    assert result["persisted"] is False
    assert result["published"] is False
    assert result["emailed"] is False
    assert result["validated_run_payload"] == _payload()


def test_daily_alert_existing_tool_can_request_validation_only(monkeypatch) -> None:
    payload = _payload()
    payload["validation_only"] = True
    captured = {}

    def validate(_session, _settings, *, as_of_date, run_payload):
        captured.update(as_of_date=as_of_date, run_payload=run_payload)
        return {"status": "valid", "persisted": False}

    monkeypatch.setattr("app.services.daily_stock_alert.validate_daily_stock_alert", validate)
    result = run_daily_stock_alert(
        object(), Settings(), as_of_date="2026-08-21", run_payload=payload
    )

    assert result == {"status": "valid", "persisted": False}
    assert captured["as_of_date"] == "2026-08-21"
    assert "validation_only" not in captured["run_payload"]


def test_daily_alert_stops_before_recording_when_data_is_stale(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.daily_stock_alert.get_data_freshness",
        lambda _session, _settings: {
            "expected_market_date": "2026-08-21",
            "ready_for_screening": False,
            "freshness_issues": ["derived_features latest run failed"],
        },
    )

    with pytest.raises(RuntimeError, match="derived_features latest run failed"):
        run_daily_stock_alert(
            object(),
            Settings(),
            as_of_date="2026-08-21",
            run_payload=_payload(),
        )


def test_daily_alert_rejects_caller_controlled_publish_flag() -> None:
    payload = _payload()
    payload["publish"] = True

    with pytest.raises(ValueError, match="must not contain publish"):
        run_daily_stock_alert(
            object(),
            Settings(),
            as_of_date="2026-08-21",
            run_payload=payload,
        )


@pytest.mark.parametrize(
    ("candidates", "expected_error"),
    [
        ([{"ticker": "AAPL"}], "missing prepared candidates: MSFT"),
        (
            [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "NVDA"}],
            "unexpected candidates: NVDA",
        ),
        (
            [{"ticker": "AAPL"}, {"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "duplicate candidates: AAPL",
        ),
    ],
)
def test_daily_alert_requires_exact_prepared_candidate_scope(
    candidates, expected_error
) -> None:
    payload = _payload()
    payload.update(
        strategy_key="dynamic_swing_buy_alerts",
        strategy_version="0.8",
        candidates=candidates,
        summary={
            "preparation_scope": {
                "expected_candidate_tickers": ["AAPL", "MSFT"]
            }
        },
    )

    with pytest.raises(ValueError, match=expected_error):
        run_daily_stock_alert(
            object(),
            Settings(),
            as_of_date="2026-08-21",
            run_payload=payload,
        )


def test_daily_alert_requires_completed_report_for_prepared_strategy() -> None:
    payload = _payload()
    payload.update(
        strategy_key="dynamic_swing_buy_alerts",
        strategy_version="0.8",
        report_markdown=None,
    )

    with pytest.raises(ValueError, match="requires completed report_markdown"):
        run_daily_stock_alert(
            object(),
            Settings(),
            as_of_date="2026-08-21",
            run_payload=payload,
        )
