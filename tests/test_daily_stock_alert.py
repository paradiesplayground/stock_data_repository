import pytest

from app.config import Settings
from app.services.daily_stock_alert import run_daily_stock_alert


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
