from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import IngestionRun


class RunTracker:
    def __init__(
        self,
        session: Session,
        job_name: str,
        source: str,
        details: dict[str, Any] | None = None,
    ):
        self.session = session
        self.run = IngestionRun(
            job_name=job_name, source=source, status="running", details=details
        )
        session.add(self.run)
        session.commit()
        session.refresh(self.run)
        self.run_id = self.run.id

    def succeed(
        self, seen: int, written: int, details: dict[str, Any] | None = None
    ) -> None:
        self.run.status = "succeeded"
        self.run.records_seen = seen
        self.run.records_written = written
        self.run.completed_at_utc = datetime.now(timezone.utc)
        if details is not None:
            self.run.details = details
        self.session.commit()

    def fail(self, error: Exception, seen: int = 0, written: int = 0) -> None:
        self.session.rollback()
        self.run = self.session.get(IngestionRun, self.run_id)
        if self.run is not None:
            self.run.status = "failed"
            self.run.records_seen = seen
            self.run.records_written = written
            self.run.completed_at_utc = datetime.now(timezone.utc)
            self.run.error_message = str(error)[:8000]
            self.session.commit()
