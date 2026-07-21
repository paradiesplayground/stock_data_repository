import argparse
from datetime import date

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.services.massive_ingestion import (
    backfill_market_data,
    sync_market_incremental,
    sync_market_day,
    sync_reference_data,
)
from app.services.sec_ingestion import (
    sync_companyfacts,
    sync_sec_all,
    sync_sec_incremental,
    sync_submissions,
)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Stock data ingestion jobs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync-reference")
    market = subparsers.add_parser("sync-market")
    market.add_argument("--date", type=_date)
    backfill = subparsers.add_parser("backfill-market")
    backfill.add_argument("--start", type=_date)
    backfill.add_argument("--end", type=_date)
    subparsers.add_parser("sync-companyfacts")
    subparsers.add_parser("sync-submissions")
    subparsers.add_parser("sync-sec")
    subparsers.add_parser("sync-sec-incremental")

    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        if args.command == "sync-reference":
            result = sync_reference_data(session, settings)
        elif args.command == "sync-market":
            result = (
                sync_market_day(session, settings, args.date)
                if args.date
                else sync_market_incremental(session, settings)
            )
        elif args.command == "backfill-market":
            result = backfill_market_data(session, settings, args.start, args.end)
        elif args.command == "sync-companyfacts":
            result = sync_companyfacts(session, settings)
        elif args.command == "sync-submissions":
            result = sync_submissions(session, settings)
        elif args.command == "sync-sec":
            result = sync_sec_all(session, settings)
        else:
            result = sync_sec_incremental(session, settings)
    print(result)


if __name__ == "__main__":
    main()
