from types import SimpleNamespace

from app.services import daily_stock_alert_workflow as workflow


class _RevisionSession:
    def __init__(self, latest=None) -> None:
        self.latest = latest
        self.added = None
        self.commits = 0

    def scalar(self, _statement):
        return self.latest

    def add(self, item) -> None:
        self.added = item
        self.latest = item

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


def _prepared() -> dict:
    return {
        "status": "prepared",
        "as_of_date": "2026-09-15",
        "strategy_key": "dynamic_swing_buy_alerts",
        "strategy_version": "0.8",
        "run_template": {
            "idempotency_key": "dynamic_swing_buy_alerts:0.8:2026-09-15:v1:cutoff",
        },
    }


def test_normal_preparation_reuses_latest_revision() -> None:
    latest = SimpleNamespace(
        preparation_id="prep-r1",
        snapshot={
            "status": "prepared",
            "preparation_revision": 1,
            "run_template": {"idempotency_key": "key:revision:1"},
        },
    )
    session = _RevisionSession(latest)

    result = workflow.persist_preparation(session, _prepared())

    assert result["preparation_id"] == "prep-r1"
    assert result["preparation_revision"] == 1
    assert result["preparation_reused"] is True
    assert session.added is None


def test_explicit_revision_creates_new_preparation_and_run_identity() -> None:
    base = SimpleNamespace(
        preparation_id="prep-base",
        snapshot={
            "status": "prepared",
            "preparation_revision": 0,
            "run_template": {"idempotency_key": "dynamic_swing_buy_alerts:0.8:2026-09-15:v1:cutoff"},
        },
    )
    session = _RevisionSession(base)

    with workflow.new_daily_stock_alert_preparation_revision("Explicit user-requested rerun"):
        result = workflow.persist_preparation(session, _prepared())

    assert result["preparation_reused"] is False
    assert result["preparation_revision"] == 1
    assert result["preparation_revision_reason"] == "Explicit user-requested rerun"
    assert result["run_template"]["idempotency_key"].endswith(":revision:1")
    assert session.added.preparation_key.endswith(":revision:1")
    assert session.added.preparation_id == result["preparation_id"]

    resumed = workflow.persist_preparation(session, _prepared())
    assert resumed["preparation_id"] == result["preparation_id"]
    assert resumed["preparation_reused"] is True


def test_repeated_explicit_rerun_advances_revision_number() -> None:
    revision_one = SimpleNamespace(
        preparation_id="prep-r1",
        snapshot={
            "status": "prepared",
            "preparation_revision": 1,
            "run_template": {"idempotency_key": "key:revision:1"},
        },
    )
    session = _RevisionSession(revision_one)

    with workflow.new_daily_stock_alert_preparation_revision("Another explicit rerun"):
        result = workflow.persist_preparation(session, _prepared())

    assert result["preparation_revision"] == 2
    assert result["run_template"]["idempotency_key"].endswith(":revision:2")
    assert session.added.preparation_key.endswith(":revision:2")


def test_revision_context_requires_reason_and_resets_after_use() -> None:
    session = _RevisionSession(None)

    try:
        with workflow.new_daily_stock_alert_preparation_revision(""):
            pass
    except ValueError as error:
        assert "non-empty reason" in str(error)
    else:
        raise AssertionError("empty revision reason should fail")

    with workflow.new_daily_stock_alert_preparation_revision("Fresh run"):
        revised = workflow.persist_preparation(session, _prepared())
    assert revised["preparation_revision"] == 1

    normal = workflow.persist_preparation(session, _prepared())
    assert normal["preparation_id"] == revised["preparation_id"]
    assert normal["preparation_reused"] is True
