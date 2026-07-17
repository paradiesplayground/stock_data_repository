import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import DailyPriceBar, Security
from app.providers.massive import MassiveClient
from app.services.runs import RunTracker

logger = logging.getLogger(__name__)


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
                        "currency": item.get("currency_name") or item.get("currency_symbol"),
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
) -> tuple[int, int]:
    tracker = RunTracker(
        session,
        "massive_daily_prices",
        "massive",
        details={"trade_date": trade_date.isoformat()},
    )
    seen = written = 0
    try:
        if client is None:
            with MassiveClient(settings) as owned_client:
                payload = owned_client.get_grouped_daily(trade_date)
        else:
            payload = client.get_grouped_daily(trade_date)
        results = payload.get("results", [])
        seen = len(results)
        if not results:
            tracker.succeed(0, 0, {"trade_date": trade_date.isoformat(), "status": payload.get("status")})
            return 0, 0

        tickers = [str(row["T"]).upper() for row in results if row.get("T")]
        placeholder_rows = [{"ticker": ticker, "active": True, "market": "stocks"} for ticker in tickers]
        session.execute(insert(Security).values(placeholder_rows).on_conflict_do_nothing(index_elements=[Security.ticker]))

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
        for start in range(0, len(rows), 1000):
            batch = rows[start : start + 1000]
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
        tracker.succeed(seen, written, {"trade_date": trade_date.isoformat(), "request_id": payload.get("request_id")})
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
    # The current trading day's daily summary may be unavailable until after
    # the close (or later, depending on the Massive plan), so backfill only
    # through yesterday unless an explicit end date is supplied.
    end = end_date or (date.today() - timedelta(days=1))
    start = start_date or (end - timedelta(days=settings.massive_backfill_days))
    total_seen = total_written = 0
    current = start
    with MassiveClient(settings) as client:
        while current <= end:
            if current.weekday() < 5:
                seen, written = sync_market_day(session, settings, current, client=client)
                total_seen += seen
                total_written += written
            current += timedelta(days=1)
    return total_seen, total_written


def _dedupe_price_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep one daily bar per ticker before the ticker/date upsert."""
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        ticker = str(row["ticker"])
        unique[ticker] = row
    return list(unique.values())
