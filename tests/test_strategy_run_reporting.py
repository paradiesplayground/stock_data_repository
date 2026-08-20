import importlib
import sys
from contextlib import contextmanager

from app.config import Settings, get_settings
from app.services.stock_alert_delivery import publish_strategy_run


def test_record_strategy_run_schema_and_service_use_top_level_report_markdown(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ENABLE_STRATEGY_WRITES", "true")
    get_settings.cache_clear()
    sys.modules.pop("app.mcp_server", None)
    mcp_server = importlib.import_module("app.mcp_server")
    tool = mcp_server.mcp._tool_manager._tools["record_strategy_run"]
    publish_tool = mcp_server.mcp._tool_manager._tools["publish_strategy_run"]

    properties = tool.parameters["properties"]
    assert "report_markdown" in properties
    assert "decision_contract_version" in properties
    assert properties["report_markdown"]["anyOf"][0] == {"type": "string"}
    assert "report_markdown" not in properties["summary"].get("properties", {})
    assert publish_tool.parameters["required"] == ["run_id"]

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

        def json(self) -> dict[str, str]:
            return {"status": "published", "email": "sent"}

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
        "email_delivery": "sent",
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
