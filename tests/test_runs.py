from datetime import datetime, timedelta, timezone

from app.models import IngestionRun
from app.services.runs import (
    SCHEDULER_SHUTDOWN_ERROR,
    interrupt_running_ingestion_runs,
    latest_run_was_interrupted,
    recover_stale_ingestion_runs,
)


class QueryRows:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args):
        return self

    def all(self):
        return self.rows


class Session:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    def query(self, _model):
        return QueryRows(self.rows)

    def commit(self):
        self.commits += 1


def test_recover_stale_ingestion_runs_closes_orphaned_worker_run() -> None:
    now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
    run = IngestionRun(
        job_name="massive_corporate_actions",
        source="massive",
        status="running",
        started_at_utc=now - timedelta(days=4),
    )
    session = Session([run])

    assert recover_stale_ingestion_runs(session, now=now) == {"massive_corporate_actions"}
    assert run.status == "failed"
    assert run.completed_at_utc == now
    assert "worker interruption" in run.error_message
    assert session.commits == 1


def test_interrupt_running_ingestion_runs_closes_fresh_run_immediately() -> None:
    now = datetime(2026, 9, 29, 10, 30, 15, tzinfo=timezone.utc)
    run = IngestionRun(
        job_name="derived_features",
        source="massive+sec-edgar",
        status="running",
        started_at_utc=now - timedelta(seconds=15),
    )
    session = Session([run])

    assert interrupt_running_ingestion_runs(session, now=now) == {"derived_features"}
    assert run.status == "failed"
    assert run.completed_at_utc == now
    assert run.error_message == SCHEDULER_SHUTDOWN_ERROR
    assert session.commits == 1


def test_latest_run_was_interrupted_requires_latest_shutdown_marker() -> None:
    interrupted = IngestionRun(
        job_name="derived_features",
        source="massive+sec-edgar",
        status="failed",
        error_message=SCHEDULER_SHUTDOWN_ERROR,
    )

    class LatestRunSession:
        def __init__(self, run):
            self.run = run

        def scalar(self, _statement):
            return self.run

    assert latest_run_was_interrupted(LatestRunSession(interrupted), "derived_features")
    interrupted.error_message = "ordinary feature calculation failure"
    assert not latest_run_was_interrupted(
        LatestRunSession(interrupted), "derived_features"
    )
