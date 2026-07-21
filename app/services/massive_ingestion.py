import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import DailyPriceBar, Security
from app.providers.massive import MassiveClient
from app.services.history import record_price_revisions, record_security_snapshots
from app.services.runs import RunTracker

logger = logging.getLogger(__name__)


class MarketDataIncomplete(RuntimeError):
    pass


def local_today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _clean_cik(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value).removeprefix("CIK").zfill(10)


def sync_reference_data(session: Session, settings: Settings) -> tuple[int, int]:
    tracker = RunTracker(session, "massive_reference", "massive")
    seen = written = 0
    try:
        with MassiveClient(settings) as client:
            batch: list[dict[str, object]] = []
            for item in client.iter_active_stock_tickers():
                seen += 1
                ticker = str(item.get("ticker", "")).upper().strip()
                if not ticker:
                    continue
                batch.append(
                    {
                        "ticker": ticker,
                        "name": item.get("name"),
                        "market": item.get("market"),
                        "locale": item.get("locale"),
                        "currency": item.get("currency_name")
                        or item.get("currency_symbol"),
                        "primary_exchange": item.get("primary_exchange"),
                        "security_type": item.get("type"),
                        "active": bool(item.get("active", True)),
                        "cik": _clean_cik(item.get("cik")),
                        "composite_figi": item.get("composite_figi"),
                        "share_class_figi": item.get("share_class_figi"),
                    }
                )
                if len(batch) >= 1000:
                    written += _upsert_securities(session, batch)
                    batch.clear()
            if batch:
                written += _upsert_securities(session, batch)
        tracker.succeed(seen, written)
        return seen, written
    except Exception as error:
        tracker.fail(error, seen, written)
        raise


def _upsert_securities(session: Session, rows: list[dict[str, object]]) -> int:
    rows = _dedupe_security_rows(rows)
    if not rows:
        return 0
    record_security_snapshots(session, rows, "massive")
    statement = insert(Security).values(rows)
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[Security.ticker],
        set_={
            "name": excluded.name,
            "market": excluded.market,
            "locale": excluded.locale,
            "currency": excluded.currency,
            "primary_exchange": excluded.primary_exchange,
            "security_type": excluded.security_type,
            "active": excluded.active,
            "cik": excluded.cik,
            "composite_figi": excluded.composite_figi,
            "share_class_figi": excluded.share_class_figi,
        },
    )
    session.execute(statement)
    session.commit()
    return len(rows)


def _dedupe_security_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep one row per ticker so a PostgreSQL upsert never targets a key twice."""
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        ticker = str(row["ticker"])
        unique[ticker] = row
    return list(unique.values())


def sync_market_day(
    session: Session,
    settings: Settings,
    trade_date: date,
    client: MassiveClient | None = None,
    validate_completeness: bool = False,
) -> tuple[int, int]:
    tracker = RunTracker(
        session,
        "massive_daily_prices",
        "massive",
        details={"trade_date": trade_date.isoformat()},
    )
    seen = written = revisions_written = 0
    try:
        if client is None:
            with MassiveClient(settings) as owned_client:
                payload = owned_client.get_grouped_daily(trade_date)
        else:
            payload = client.get_grouped_daily(trade_date)
        results = payload.get("results", [])
        seen = len(results)
        if not results:
            if validate_completeness:
                minimum = _minimum_daily_results(session, settings, trade_date)
                raise MarketDataIncomplete(
                    f"Massive returned 0 usable rows for {trade_date}; expected at least {minimum}"
                )
            tracker.succeed(
                0,
                0,
                {"trade_date": trade_date.isoformat(), "status": payload.get("status")},
            )
            return 0, 0

        tickers = [str(row["T"]).upper() for row in results if row.get("T")]
        placeholder_rows = [
            {"ticker": ticker, "active": True, "market": "stocks"} for ticker in tickers
        ]
        session.execute(
            insert(Security)
            .values(placeholder_rows)
            .on_conflict_do_nothing(index_elements=[Security.ticker])
        )

        rows = [
            {
                "ticker": str(row["T"]).upper(),
                "trade_date": trade_date,
                "open": Decimal(str(row["o"])),
                "high": Decimal(str(row["h"])),
                "low": Decimal(str(row["l"])),
                "close": Decimal(str(row["c"])),
                "volume": Decimal(str(row["v"])),
                "vwap": Decimal(str(row["vw"])) if row.get("vw") is not None else None,
                "transactions": row.get("n"),
                "adjusted": bool(payload.get("adjusted", True)),
                "source": "massive",
                "source_timestamp_ms": row.get("t"),
            }
            for row in results
            if all(key in row for key in ("T", "o", "h", "l", "c", "v"))
        ]
        rows = _dedupe_price_rows(rows)
        if validate_completeness:
            minimum = _minimum_daily_results(session, settings, trade_date)
            if len(rows) < minimum:
                raise MarketDataIncomplete(
                    f"Massive returned {len(rows)} usable rows for {trade_date}; "
                    f"expected at least {minimum}"
                )
        for start in range(0, len(rows), 1000):
            batch = rows[start : start + 1000]
            revisions_written += record_price_revisions(session, batch)
            statement = insert(DailyPriceBar).values(batch)
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                constraint="uq_daily_price_ticker_date",
                set_={
                    "open": excluded.open,
                    "high": excluded.high,
                    "low": excluded.low,
                    "close": excluded.close,
                    "volume": excluded.volume,
                    "vwap": excluded.vwap,
                    "transactions": excluded.transactions,
                    "adjusted": excluded.adjusted,
                    "source_timestamp_ms": excluded.source_timestamp_ms,
                },
            )
            session.execute(statement)
            session.commit()
            written += len(batch)
        tracker.succeed(
            seen,
            written,
            {
                "trade_date": trade_date.isoformat(),
                "request_id": payload.get("request_id"),
                "price_revisions_written": revisions_written,
            },
        )
        return seen, written
    except Exception as error:
        tracker.fail(error, seen, written)
        raise


def backfill_market_data(
    session: Session,
    settings: Settings,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[int, int]:
    # The current trading day's daily summary may be unavailable until the
    # following day, depending on the Massive plan. Default to the latest
    # eligible weekday strictly before today.
    end = end_date or market_target_date(
        local_today(settings), max(1, settings.massive_market_lag_days)
    )
    start = start_date or (end - timedelta(days=settings.massive_backfill_days))
    total_seen = total_written = 0
    current = start
    with MassiveClient(settings) as client:
        while current <= end:
            if current.weekday() < 5:
                seen, written = sync_market_day(
                    session, settings, current, client=client
                )
                total_seen += seen
                total_written += written
            current += timedelta(days=1)
    return total_seen, total_written


def market_target_date(as_of: date, lag_days: int) -> date:
    """Return the configured weekday target on or before ``as_of``."""
    candidate = as_of - timedelta(days=lag_days)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def latest_eligible_market_date(as_of: date | None = None) -> date:
    """Compatibility helper returning the latest weekday before ``as_of``."""
    return market_target_date(as_of or date.today(), 1)


def market_dates_to_sync(latest_stored: date | None, end_date: date) -> list[date]:
    """Return missing weekdays through ``end_date`` for incremental catch-up."""
    current = (latest_stored + timedelta(days=1)) if latest_stored else end_date
    dates: list[date] = []
    while current <= end_date:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def sync_market_incremental(
    session: Session,
    settings: Settings,
    as_of: date | None = None,
) -> tuple[int, int]:
    """Catch up every missing weekday through the latest eligible market date."""
    local_date = as_of or local_today(settings)
    end = market_target_date(local_date, settings.massive_market_lag_days)
    latest_stored = session.scalar(select(func.max(DailyPriceBar.trade_date)))
    dates = market_dates_to_sync(latest_stored, end)
    if not dates:
        logger.info("Massive daily prices already current through %s", end)
        return 0, 0

    total_seen = total_written = 0
    with MassiveClient(settings) as client:
        for trade_date in dates:
            is_same_day_target = (
                settings.massive_market_lag_days == 0
                and trade_date == local_date
                and trade_date.weekday() < 5
            )
            attempts = settings.massive_eod_retry_attempts if is_same_day_target else 1
            for attempt in range(1, attempts + 1):
                try:
                    seen, written = sync_market_day(
                        session,
                        settings,
                        trade_date,
                        client=client,
                        validate_completeness=is_same_day_target,
                    )
                    break
                except Exception as error:
                    retryable = isinstance(error, MarketDataIncomplete) or (
                        isinstance(error, httpx.HTTPStatusError)
                        and error.response.status_code == 403
                    )
                    if not retryable or attempt == attempts:
                        raise
                    logger.warning(
                        "Massive data for %s is not ready; retrying in %ss (%s/%s)",
                        trade_date,
                        settings.massive_eod_retry_seconds,
                        attempt,
                        attempts,
                    )
                    time.sleep(settings.massive_eod_retry_seconds)
            total_seen += seen
            total_written += written
    return total_seen, total_written


def _minimum_daily_results(
    session: Session, settings: Settings, trade_date: date
) -> int:
    previous_date = session.scalar(
        select(func.max(DailyPriceBar.trade_date)).where(
            DailyPriceBar.trade_date < trade_date
        )
    )
    if previous_date is None:
        return settings.massive_min_daily_results
    previous_count = (
        session.scalar(
            select(func.count(DailyPriceBar.id)).where(
                DailyPriceBar.trade_date == previous_date
            )
        )
        or 0
    )
    coverage_minimum = int(previous_count * settings.massive_min_daily_coverage_ratio)
    return max(settings.massive_min_daily_results, coverage_minimum)


def _dedupe_price_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep one daily bar per ticker before the ticker/date upsert."""
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        ticker = str(row["ticker"])
        unique[ticker] = row
    return list(unique.values())
