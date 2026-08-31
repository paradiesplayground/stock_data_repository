import importlib
import json
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import Settings, get_settings
from app.services.stock_alert_delivery import (
    publish_strategy_run_revision,
    publish_strategy_run,
    resend_strategy_run_email,
    validate_strategy_run_for_delivery,
    verify_strategy_run_email,
)
from app.services.strategy_tracking import configuration_fingerprint, record_strategy_run


def test_configuration_fingerprint_normalizes_equivalent_json_numbers() -> None:
    prepared = {
        "minimum_price": 5,
        "minimum_market_cap": 100_000_000,
        "threshold": 0.25,
    }
    transported = {
        "minimum_price": 5.0,
        "minimum_market_cap": Decimal("100000000.000"),
        "threshold": Decimal("0.2500"),
    }
    changed = {**prepared, "minimum_price": 6}

    assert configuration_fingerprint(prepared) == configuration_fingerprint(transported)
    assert configuration_fingerprint(prepared) != configuration_fingerprint(changed)


def test_record_strategy_run_schema_and_service_use_top_level_report_markdown(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ENABLE_STRATEGY_WRITES", "true")
    get_settings.cache_clear()
    sys.modules.pop("app.mcp_server", None)
    mcp_server = importlib.import_module("app.mcp_server")
    tool = mcp_server.mcp._tool_manager._tools["record_strategy_run"]
    prepare_tool = mcp_server.mcp._tool_manager._tools["prepare_daily_stock_alert"]
    validate_tool = mcp_server.mcp._tool_manager._tools["validate_daily_stock_alert"]
    publish_tool = mcp_server.mcp._tool_manager._tools["publish_strategy_run"]
    resend_tool = mcp_server.mcp._tool_manager._tools["resend_strategy_run_email"]
    revision_tool = mcp_server.mcp._tool_manager._tools["publish_strategy_run_revision"]
    verify_tool = mcp_server.mcp._tool_manager._tools["verify_strategy_run_email"]

    properties = tool.parameters["properties"]
    assert "report_markdown" in properties
    assert "decision_contract_version" in properties
    assert properties["report_markdown"]["anyOf"][0] == {"type": "string"}
    assert "report_markdown" not in properties["summary"].get("properties", {})
    assert prepare_tool.parameters["required"] == ["as_of_date"]
    assert validate_tool.parameters["required"] == ["as_of_date", "run_payload"]
    for read_only_tool in (prepare_tool, validate_tool):
        assert read_only_tool.annotations is not None
        assert read_only_tool.annotations.readOnlyHint is True
        assert read_only_tool.annotations.destructiveHint is False
        assert read_only_tool.annotations.idempotentHint is True
        assert read_only_tool.annotations.openWorldHint is False
    assert publish_tool.parameters["required"] == ["run_id"]
    assert resend_tool.parameters["required"] == ["run_id"]
    assert revision_tool.parameters["required"] == [
        "run_id",
        "renderer_version",
        "reason",
    ]
    assert verify_tool.parameters["required"] == ["run_id"]

    captured = {}

    @contextmanager
    def fake_session_local():
        yield object()

    def fake_save_strategy_run(_session, **payload):
        captured.update(payload)
        return {"recorded": True}

    monkeypatch.setattr(mcp_server, "SessionLocal", fake_session_local)
    monkeypatch.setattr(mcp_server, "save_strategy_run", fake_save_strategy_run)

    result = tool.fn(
        strategy_key="daily-alert",
        strategy_version="1.0.0",
        as_of_date="2026-08-04",
        run_type="as_run",
        idempotency_key="daily-alert:2026-08-04",
        configuration={},
        filters={},
        candidates=[],
        summary={"candidate_count": 0},
        report_markdown="# Daily alert\n\nNo actionable entries.",
        decision_contract_version="0.7",
    )

    assert result == {"recorded": True}
    assert captured["summary"] == {"candidate_count": 0}
    assert "report_markdown" not in captured["summary"]
    assert captured["report_markdown"].startswith("# Daily alert")
    assert captured["publish"] is False

    get_settings.cache_clear()
    sys.modules.pop("app.mcp_server", None)


def test_validate_only_checks_immutable_definition_without_writing() -> None:
    class ValidationSession:
        def __init__(self) -> None:
            self.scalar_calls = 0

        def scalar(self, _statement):
            self.scalar_calls += 1
            return type(
                "Definition",
                (),
                {"configuration": {}, "skill_fingerprint": None},
            )()

    session = ValidationSession()
    result = record_strategy_run(
        session,
        strategy_key="daily-alert",
        strategy_version="0.8",
        as_of_date="2026-08-21",
        run_type="as_run",
        idempotency_key="daily-alert:0.8:2026-08-21",
        configuration={},
        filters={},
        candidates=[],
        summary={},
        report_markdown="# Daily alert\n\nNo trade today.",
        evidence=[],
        decision_contract_version="0.8",
        publish=False,
        validate_only=True,
    )

    assert result["status"] == "valid"
    assert result["candidate_count"] == 0
    assert result["persisted"] is False
    assert result["published"] is False
    assert result["emailed"] is False
    assert len(result["payload_hash"]) == 64
    assert session.scalar_calls == 1


def test_validate_only_rejects_existing_version_with_different_configuration() -> None:
    class ExistingDefinitionSession:
        def scalar(self, _statement):
            return type(
                "Definition",
                (),
                {
                    "configuration": {"minimum_revenue_growth_pct": 30},
                    "skill_fingerprint": None,
                },
            )()

    with pytest.raises(ValueError, match="configuration changed"):
        record_strategy_run(
            ExistingDefinitionSession(),
            strategy_key="daily-alert",
            strategy_version="0.8",
            as_of_date="2026-08-21",
            run_type="as_run",
            idempotency_key="daily-alert:0.8:2026-08-21",
            configuration={"minimum_revenue_growth_pct": 40},
            filters={},
            candidates=[],
            summary={},
            report_markdown="# Daily alert\n\nNo trade today.",
            evidence=[],
            decision_contract_version="0.8",
            publish=False,
            validate_only=True,
        )


def test_website_delivery_sends_report_markdown_at_top_level(monkeypatch) -> None:
    report = "# Daily alert\n\nNo actionable entries."
    run = {
        "found": True,
        "run_id": "run-1",
        "run_type": "as_run",
        "decision_contract_version": "0.7",
        "summary": {"candidate_count": 0},
        "report_markdown": report,
        "candidates": [],
    }
    monkeypatch.setattr(
        "app.services.stock_alert_delivery.get_strategy_run",
        lambda _session, _run_id: run,
    )
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "published",
                "email": "sent",
                "email_receipt": {
                    "message_id": "message-1",
                    "accepted_count": 1,
                    "rejected_count": 0,
                },
            }

    def fake_post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.stock_alert_delivery.httpx.post", fake_post)

    result = publish_strategy_run(
        object(),
        Settings(
            stock_alert_webhook_url="https://example.test/stock-alert",
            stock_alert_webhook_token="secret",
        ),
        "run-1",
    )

    assert result == {
        "status": "published",
        "run_id": "run-1",
        "website_delivery": "published",
        "email_delivery": "smtp_accepted",
        "email_receipt": {
            "message_id": "message-1",
            "accepted_count": 1,
            "rejected_count": 0,
        },
    }
    assert captured["report_markdown"] == report
    assert captured["summary"] == {"candidate_count": 0}
    assert "report_markdown" not in captured["summary"]


def test_delivery_requires_explicit_email_confirmation(monkeypatch) -> None:
    run = {
        "found": True,
        "run_id": "run-1",
        "run_type": "as_run",
        "decision_contract_version": "0.7",
        "summary": {},
        "report_markdown": "# Daily alert",
        "candidates": [],
    }
    monkeypatch.setattr(
        "app.services.stock_alert_delivery.get_strategy_run",
        lambda _session, _run_id: run,
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "published"}

    monkeypatch.setattr(
        "app.services.stock_alert_delivery.httpx.post",
        lambda *_args, **_kwargs: Response(),
    )

    import pytest

    with pytest.raises(RuntimeError, match="email delivery: missing"):
        publish_strategy_run(
            object(),
            Settings(
                stock_alert_webhook_url="https://example.test/stock-alert",
                stock_alert_webhook_token="secret",
            ),
            "run-1",
        )


def test_explicit_resend_preserves_run_and_requests_email_retry(monkeypatch) -> None:
    run = {
        "found": True,
        "run_id": "run-1",
        "run_type": "as_run",
        "decision_contract_version": "0.7",
        "summary": {},
        "report_markdown": "# Daily alert",
        "candidates": [],
    }
    monkeypatch.setattr(
        "app.services.stock_alert_delivery.get_strategy_run",
        lambda _session, _run_id: run,
    )
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "published",
                "publication": "existing",
                "email": "sent",
                "email_receipt": {
                    "message_id": "resend-2",
                    "accepted_count": 1,
                    "rejected_count": 0,
                },
            }

    def fake_post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.stock_alert_delivery.httpx.post", fake_post)

    result = resend_strategy_run_email(
        object(),
        Settings(
            stock_alert_webhook_url="https://example.test/stock-alert",
            stock_alert_webhook_token="secret",
        ),
        "run-1",
    )

    assert captured["run_id"] == "run-1"
    assert captured["delivery_request"] == "resend_email"
    assert result == {
        "status": "resent",
        "run_id": "run-1",
        "website_delivery": "existing",
        "email_delivery": "smtp_accepted",
        "email_receipt": {
            "message_id": "resend-2",
            "accepted_count": 1,
            "rejected_count": 0,
        },
    }


def test_delivery_revalidates_august_19_trade_plan_math() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "august_19_v07.json").read_text()
    )
    validate_strategy_run_for_delivery(fixture)
    fixture["candidates"][0]["trade_plan"]["planned_risk"] = 1

    with pytest.raises(ValueError, match="planned_risk.*inconsistent"):
        validate_strategy_run_for_delivery(fixture)


def test_rendering_revision_preserves_source_and_records_reason(monkeypatch) -> None:
    run = {
        "found": True,
        "run_id": "run-1",
        "run_type": "as_run",
        "decision_contract_version": "0.7",
        "candidates": [],
    }
    monkeypatch.setattr(
        "app.services.stock_alert_delivery.get_strategy_run",
        lambda _session, _run_id: run,
    )
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "published",
                "publication": "revised",
                "rendering_revision": {"revision_number": 2},
            }

    def fake_post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.stock_alert_delivery.httpx.post", fake_post)
    result = publish_strategy_run_revision(
        object(),
        Settings(
            stock_alert_webhook_url="https://example.test/stock-alert",
            stock_alert_webhook_token="secret",
        ),
        "run-1",
        "0.7.2",
        "Correct presentation labels",
    )

    assert captured["delivery_request"] == "publish_revision"
    assert captured["rendering_revision"] == {
        "renderer_version": "0.7.2",
        "reason": "Correct presentation labels",
        "resend_email": False,
    }
    assert result["status"] == "revised"


def test_mailbox_verification_returns_auditable_status(monkeypatch) -> None:
    run = {
        "found": True,
        "run_id": "run-1",
        "run_type": "as_run",
        "decision_contract_version": "0.7",
        "candidates": [],
    }
    monkeypatch.setattr(
        "app.services.stock_alert_delivery.get_strategy_run",
        lambda _session, _run_id: run,
    )
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "published",
                "mailbox_verification": {
                    "status": "verified",
                    "checked_at": "2026-08-20T12:00:00Z",
                },
            }

    def fake_post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.stock_alert_delivery.httpx.post", fake_post)
    result = verify_strategy_run_email(
        object(),
        Settings(
            stock_alert_webhook_url="https://example.test/stock-alert",
            stock_alert_webhook_token="secret",
        ),
        "run-1",
    )

    assert captured["delivery_request"] == "verify_email"
    assert result["status"] == "verified"
