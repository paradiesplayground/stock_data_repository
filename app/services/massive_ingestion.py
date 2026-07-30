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
from app.models import (
    CashDividend,
    DailyPriceBar,
    IngestionCheckpoint,
    RawDailyPriceBar,
    Security,
    SecurityReferenceSnapshot,
    StockSplit,
    TickerEvent,
)
from app.providers.massive import MassiveClient
from app.services.history import (
    record_hash,
    record_price_revisions,
    record_security_snapshots,
)
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


def _parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value)[:10])


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _reference_row(item: dict[str, object], requested_active: bool) -> dict[str, object]:
    ticker = str(item.get("ticker", "")).upper().strip()
    active_value = item.get("active")
    return {
        "ticker": ticker,
        "name": item.get("name"),
        "market": item.get("market"),
        "locale": item.get("locale"),
        "currency": item.get("currency_name") or item.get("currency_symbol"),
        "primary_exchange": item.get("primary_exchange"),
        "security_type": item.get("type"),
        "active": requested_active if active_value is None else bool(active_value),
        "cik": _clean_cik(item.get("cik")),
        "composite_figi": item.get("composite_figi"),
        "share_class_figi": item.get("share_class_figi"),
        "list_date": _parse_date(item.get("list_date")),
        "delisted_date": _parse_date(item.get("delisted_utc")),
        "sic_code": str(item.get("sic_code")) if item.get("sic_code") else None,
        "sic_description": item.get("sic_description"),
    }


def sync_reference_data(
    session: Session,
    settings: Settings,
    include_inactive: bool = True,
) -> tuple[int, int]:
    tracker = RunTracker(
        session,
        "massive_reference",
        "massive",
        details={"include_inactive": include_inactive},
    )
    seen = written = 0
    try:
        with MassiveClient(settings) as client:
            batch: list[dict[str, object]] = []
            active_states = (True, False) if include_inactive else (True,)
            for requested_active in active_states:
                for item in client.iter_stock_tickers(active=requested_active):
                    seen += 1
                    ticker = str(item.get("ticker", "")).upper().strip()
                    if not ticker:
                        continue
                    batch.append(_reference_row(item, requested_active))
                    if len(batch) >= 1000:
                        written += _upsert_securities(session, batch)
                        batch.clear()
            if batch:
                written += _upsert_securities(session, batch)
        tracker.succeed(
            seen,
            written,
            {"include_inactive": include_inactive},
        )
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
            "name": func.coalesce(excluded.name, Security.name),
            "market": func.coalesce(excluded.market, Security.market),
            "locale": func.coalesce(excluded.locale, Security.locale),
            "currency": func.coalesce(excluded.currency, Security.currency),
            "primary_exchange": func.coalesce(
                excluded.primary_exchange,
                Security.primary_exchange,
            ),
            "security_type": func.coalesce(
                excluded.security_type,
                Security.security_type,
            ),
            "active": excluded.active,
            "cik": func.coalesce(excluded.cik, Security.cik),
            "composite_figi": func.coalesce(
                excluded.composite_figi,
                Security.composite_figi,
            ),
            "share_class_figi": func.coalesce(
                excluded.share_class_figi,
                Security.share_class_figi,
            ),
            "list_date": func.coalesce(excluded.list_date, Security.list_date),
            "delisted_date": func.coalesce(
                excluded.delisted_date,
                Security.delisted_date,
            ),
            "sic_code": func.coalesce(excluded.sic_code, Security.sic_code),
            "sic_description": func.coalesce(
                excluded.sic_description,
                Security.sic_description,
            ),
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


def _reference_snapshot_rows(
    client: MassiveClient,
    as_of_date: date,
    include_inactive: bool,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    active_states = (True, False) if include_inactive else (True,)
    for requested_active in active_states:
        for item in client.iter_stock_tickers(
            active=requested_active,
            as_of_date=as_of_date,
        ):
            row = _reference_row(item, requested_active)
            if row["ticker"]:
                rows.append(row)
    return _dedupe_security_rows(rows)


def _upsert_reference_snapshots(
    session: Session,
    as_of_date: date,
    rows: list[dict[str, object]],
    latest_hashes: dict[str, str],
) -> int:
    values: list[dict[str, object]] = []
    for row in rows:
        snapshot = {
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in row.items()
        }
        snapshot_hash = record_hash(snapshot)
        ticker = str(row["ticker"])
        if latest_hashes.get(ticker) == snapshot_hash:
            continue
        latest_hashes[ticker] = snapshot_hash
        values.append(
            {
                "ticker": ticker,
                "as_of_date": as_of_date,
                "source": "massive",
                "record_hash": snapshot_hash,
                "snapshot": snapshot,
            }
        )
    if not values:
        return 0
    total = 0
    for start in range(0, len(values), 1000):
        batch = values[start : start + 1000]
        statement = insert(SecurityReferenceSnapshot).values(batch)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            constraint="uq_security_reference_snapshot",
            set_={
                "record_hash": excluded.record_hash,
                "snapshot": excluded.snapshot,
                "ingested_at_utc": func.now(),
            },
        )
        session.execute(statement)
        session.commit()
        total += len(batch)
    return total


def backfill_reference_snapshots(
    session: Session,
    settings: Settings,
    start_date: date,
    end_date: date,
    *,
    include_inactive: bool = True,
    resume: bool = False,
) -> dict[str, object]:
    tracker = RunTracker(
        session,
        "massive_reference_backfill",
        "massive",
        details={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "include_inactive": include_inactive,
            "resume": resume,
        },
    )
    checkpoint_key = "massive_reference_backfill"
    checkpoint = session.get(IngestionCheckpoint, checkpoint_key)
    effective_start = start_date
    if resume and checkpoint is not None:
        effective_start = max(start_date, checkpoint.checkpoint_date + timedelta(days=1))
    market_sessions = session.scalars(
        select(DailyPriceBar.trade_date)
        .where(
            DailyPriceBar.ticker == "QQQ",
            DailyPriceBar.trade_date >= effective_start,
            DailyPriceBar.trade_date <= end_date,
        )
        .distinct()
        .order_by(DailyPriceBar.trade_date)
    ).all()
    seen = written = 0
    completed_dates: list[str] = []
    try:
        prior_rows = session.scalars(
            select(SecurityReferenceSnapshot)
            .where(SecurityReferenceSnapshot.as_of_date < effective_start)
            .distinct(SecurityReferenceSnapshot.ticker)
            .order_by(
                SecurityReferenceSnapshot.ticker,
                SecurityReferenceSnapshot.as_of_date.desc(),
                SecurityReferenceSnapshot.id.desc(),
            )
        ).all()
        latest_hashes = {row.ticker: row.record_hash for row in prior_rows}
        with MassiveClient(settings) as client:
            for snapshot_date in market_sessions:
                rows = _reference_snapshot_rows(
                    client,
                    snapshot_date,
                    include_inactive,
                )
                seen += len(rows)
                written += _upsert_reference_snapshots(
                    session,
                    snapshot_date,
                    rows,
                    latest_hashes,
                )
                completed_dates.append(snapshot_date.isoformat())
                if checkpoint is None:
                    checkpoint = IngestionCheckpoint(
                        job_name=checkpoint_key,
                        checkpoint_date=snapshot_date,
                        details={},
                    )
                    session.add(checkpoint)
                checkpoint.checkpoint_date = snapshot_date
                checkpoint.details = {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "include_inactive": include_inactive,
                    "last_completed_date": snapshot_date.isoformat(),
                }
                session.commit()
                logger.info(
                    "Stored Massive reference snapshot %s (%s rows)",
                    snapshot_date,
                    len(rows),
                )
        details = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "market_sessions": len(market_sessions),
            "completed_dates": completed_dates,
            "include_inactive": include_inactive,
        }
        tracker.succeed(seen, written, details)
        return {**details, "records_seen": seen, "records_written": written}
    except Exception as error:
        tracker.fail(error, seen, written)
        raise


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
                payload = owned_client.get_grouped_daily(
                    trade_date,
                    adjusted=True,
                )
                raw_payload = owned_client.get_grouped_daily(
                    trade_date,
                    adjusted=False,
                )
        else:
            payload = client.get_grouped_daily(trade_date, adjusted=True)
            raw_payload = client.get_grouped_daily(trade_date, adjusted=False)
        results = payload.get("results", [])
        raw_results = raw_payload.get("results", [])
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
        if not raw_results:
            raise MarketDataIncomplete(
                f"Massive returned adjusted bars but 0 unadjusted bars for {trade_date}"
            )

        tickers = {
            str(row["T"]).upper()
            for row in [*results, *raw_results]
            if row.get("T")
        }
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
        raw_rows = _dedupe_price_rows(
            [
                {
                    "ticker": str(row["T"]).upper(),
                    "trade_date": trade_date,
                    "open": Decimal(str(row["o"])),
                    "high": Decimal(str(row["h"])),
                    "low": Decimal(str(row["l"])),
                    "close": Decimal(str(row["c"])),
                    "volume": Decimal(str(row["v"])),
                    "vwap": (
                        Decimal(str(row["vw"])) if row.get("vw") is not None else None
                    ),
                    "transactions": row.get("n"),
                    "source": "massive",
                    "source_timestamp_ms": row.get("t"),
                }
                for row in raw_results
                if all(key in row for key in ("T", "o", "h", "l", "c", "v"))
            ]
        )
        if validate_completeness:
            minimum = _minimum_daily_results(session, settings, trade_date)
            if len(rows) < minimum:
                raise MarketDataIncomplete(
                    f"Massive returned {len(rows)} usable rows for {trade_date}; "
                    f"expected at least {minimum}"
                )
            if len(raw_rows) < minimum:
                raise MarketDataIncomplete(
                    f"Massive returned {len(raw_rows)} usable unadjusted rows for "
                    f"{trade_date}; expected at least {minimum}"
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
        for start in range(0, len(raw_rows), 1000):
            batch = raw_rows[start : start + 1000]
            statement = insert(RawDailyPriceBar).values(batch)
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                constraint="uq_raw_daily_price_ticker_date",
                set_={
                    "open": excluded.open,
                    "high": excluded.high,
                    "low": excluded.low,
                    "close": excluded.close,
                    "volume": excluded.volume,
                    "vwap": excluded.vwap,
                    "transactions": excluded.transactions,
                    "source_timestamp_ms": excluded.source_timestamp_ms,
                    "ingested_at_utc": func.now(),
                },
            )
            session.execute(statement)
            session.commit()
        tracker.succeed(
            seen,
            written,
            {
                "trade_date": trade_date.isoformat(),
                "request_id": payload.get("request_id"),
                "raw_request_id": raw_payload.get("request_id"),
                "raw_rows_written": len(raw_rows),
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


def _provider_id(prefix: str, row: dict[str, object]) -> str:
    value = row.get("id")
    if value not in (None, ""):
        return str(value)
    return f"{prefix}:{record_hash(row)}"


def sync_corporate_actions(
    session: Session,
    settings: Settings,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, int | str | None]:
    tracker = RunTracker(
        session,
        "massive_corporate_actions",
        "massive",
        details={
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
    )
    seen = written = 0
    split_rows: list[dict[str, object]] = []
    dividend_rows: list[dict[str, object]] = []
    try:
        with MassiveClient(settings) as client:
            for row in client.iter_splits(start_date, end_date):
                execution_date = _parse_date(row.get("execution_date"))
                ticker = str(row.get("ticker", "")).upper().strip()
                if not execution_date or not ticker:
                    continue
                seen += 1
                split_rows.append(
                    {
                        "provider_id": _provider_id("split", row),
                        "ticker": ticker,
                        "execution_date": execution_date,
                        "split_from": _decimal(row.get("split_from")),
                        "split_to": _decimal(row.get("split_to")),
                        "adjustment_type": row.get("adjustment_type"),
                        "historical_adjustment_factor": _decimal(
                            row.get("historical_adjustment_factor")
                        ),
                        "source": "massive",
                    }
                )
            for row in client.iter_dividends(start_date, end_date):
                ex_dividend_date = _parse_date(row.get("ex_dividend_date"))
                ticker = str(row.get("ticker", "")).upper().strip()
                if not ex_dividend_date or not ticker:
                    continue
                seen += 1
                dividend_rows.append(
                    {
                        "provider_id": _provider_id("dividend", row),
                        "ticker": ticker,
                        "ex_dividend_date": ex_dividend_date,
                        "cash_amount": _decimal(row.get("cash_amount")),
                        "split_adjusted_cash_amount": _decimal(
                            row.get("split_adjusted_cash_amount")
                        ),
                        "currency": row.get("currency"),
                        "declaration_date": _parse_date(row.get("declaration_date")),
                        "record_date": _parse_date(row.get("record_date")),
                        "pay_date": _parse_date(row.get("pay_date")),
                        "frequency": row.get("frequency"),
                        "distribution_type": row.get("distribution_type"),
                        "historical_adjustment_factor": _decimal(
                            row.get("historical_adjustment_factor")
                        ),
                        "source": "massive",
                    }
                )
        for model, rows in ((StockSplit, split_rows), (CashDividend, dividend_rows)):
            for start in range(0, len(rows), 1000):
                batch = rows[start : start + 1000]
                statement = insert(model).values(batch)
                excluded = statement.excluded
                update_columns = {
                    column.name: getattr(excluded, column.name)
                    for column in model.__table__.columns
                    if column.name not in {"provider_id", "ingested_at_utc"}
                }
                update_columns["ingested_at_utc"] = func.now()
                statement = statement.on_conflict_do_update(
                    index_elements=[model.provider_id],
                    set_=update_columns,
                )
                session.execute(statement)
                session.commit()
                written += len(batch)
        details = {
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "splits_written": len(split_rows),
            "dividends_written": len(dividend_rows),
        }
        tracker.succeed(seen, written, details)
        return {**details, "records_seen": seen, "records_written": written}
    except Exception as error:
        tracker.fail(error, seen, written)
        raise


def sync_ticker_events(
    session: Session,
    settings: Settings,
    *,
    resume: bool = False,
) -> dict[str, int | str | None]:
    tracker = RunTracker(
        session,
        "massive_ticker_events",
        "massive",
        details={"resume": resume},
    )
    checkpoint_key = "massive_ticker_events"
    checkpoint = session.get(IngestionCheckpoint, checkpoint_key)
    last_identifier = (
        str((checkpoint.details or {}).get("last_identifier", ""))
        if resume and checkpoint is not None
        else ""
    )
    identifiers = session.execute(
        select(Security.composite_figi, Security.ticker)
        .where(Security.market == "stocks")
        .order_by(Security.composite_figi, Security.ticker)
    ).all()
    asset_map: dict[str, str] = {}
    for composite_figi, ticker in identifiers:
        if composite_figi or ticker:
            asset_map[str(composite_figi or ticker)] = str(ticker)
    assets = sorted(asset_map.items())
    if last_identifier:
        assets = [asset for asset in assets if asset[0] > last_identifier]
    seen = written = processed = 0
    try:
        with MassiveClient(settings) as client:
            for identifier, current_ticker in assets:
                payload = client.get_ticker_events(identifier)
                results = payload.get("results") or {}
                name = results.get("name")
                events = results.get("events") or []
                rows: list[dict[str, object]] = []
                for event in events:
                    event_date = _parse_date(event.get("date"))
                    event_type = str(event.get("type", "")).strip()
                    if not event_date or not event_type:
                        continue
                    event_ticker = (
                        (event.get("ticker_change") or {}).get("ticker")
                        if isinstance(event.get("ticker_change"), dict)
                        else None
                    )
                    safe_event = {
                        key: value.isoformat() if isinstance(value, date) else value
                        for key, value in event.items()
                    }
                    rows.append(
                        {
                            "event_id": record_hash(
                                {
                                    "identifier": identifier,
                                    "event": safe_event,
                                }
                            ),
                            "identifier": identifier,
                            "entity_name": name,
                            "event_type": event_type,
                            "event_date": event_date,
                            "ticker": (
                                str(event_ticker).upper()
                                if event_ticker
                                else current_ticker
                            ),
                            "details": safe_event,
                            "source": "massive",
                        }
                    )
                seen += len(rows)
                if rows:
                    statement = insert(TickerEvent).values(rows)
                    excluded = statement.excluded
                    statement = statement.on_conflict_do_update(
                        index_elements=[TickerEvent.event_id],
                        set_={
                            "entity_name": excluded.entity_name,
                            "ticker": excluded.ticker,
                            "details": excluded.details,
                            "ingested_at_utc": func.now(),
                        },
                    )
                    session.execute(statement)
                    written += len(rows)
                processed += 1
                if checkpoint is None:
                    checkpoint = IngestionCheckpoint(
                        job_name=checkpoint_key,
                        checkpoint_date=local_today(settings),
                        details={},
                    )
                    session.add(checkpoint)
                checkpoint.checkpoint_date = local_today(settings)
                checkpoint.details = {
                    "last_identifier": identifier,
                    "assets_processed": processed,
                    "assets_total": len(assets),
                }
                session.commit()
                if processed % 250 == 0:
                    logger.info(
                        "Ticker events progress: %s/%s assets, %s events",
                        processed,
                        len(assets),
                        seen,
                    )
        details = {
            "assets_processed": processed,
            "assets_total": len(assets),
            "last_identifier": assets[-1][0] if assets else last_identifier or None,
        }
        tracker.succeed(seen, written, details)
        return {**details, "records_seen": seen, "records_written": written}
    except Exception as error:
        tracker.fail(error, seen, written)
        raise


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
