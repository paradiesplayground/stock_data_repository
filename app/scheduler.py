import logging
import signal
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.services.massive_ingestion import sync_market_day, sync_reference_data
from app.services.sec_ingestion import sync_sec_incremental

logger = logging.getLogger(__name__)


def _run_reference() -> None:
    with SessionLocal() as session:
        sync_reference_data(session, get_settings())


def _run_market() -> None:
    settings = get_settings()
    trade_date = datetime.now(ZoneInfo(settings.timezone)).date()
    with SessionLocal() as session:
        sync_market_day(session, settings, trade_date)


def _run_sec() -> None:
    with SessionLocal() as session:
        sync_sec_incremental(session, get_settings())


def main() -> None:
    configure_logging()
    settings = get_settings()
    scheduler = BlockingScheduler(timezone=settings.timezone)
    common = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600}
    scheduler.add_job(
        _run_reference,
        CronTrigger.from_crontab(settings.reference_sync_cron, timezone=settings.timezone),
        id="massive_reference",
        **common,
    )
    scheduler.add_job(
        _run_market,
        CronTrigger.from_crontab(settings.market_sync_cron, timezone=settings.timezone),
        id="massive_daily_prices",
        **common,
    )
    scheduler.add_job(
        _run_sec,
        CronTrigger.from_crontab(settings.sec_sync_cron, timezone=settings.timezone),
        id="sec_bulk",
        **common,
    )
    signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown(wait=False))
    logger.info("Starting ingestion scheduler in %s", settings.timezone)
    scheduler.start()


if __name__ == "__main__":
    main()
