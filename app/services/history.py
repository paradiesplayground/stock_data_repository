import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    DailyPriceBar,
    DailyPriceBarRevision,
    SecurityReferenceHistory,
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def record_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        _json_safe(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def record_security_snapshots(
    session: Session,
    rows: Iterable[dict[str, Any]],
    source: str,
) -> int:
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        snapshot = _json_safe({**row, "ticker": ticker})
        snapshots.append(
            {
                "ticker": ticker,
                "source": source,
                "record_hash": record_hash(snapshot),
                "snapshot": snapshot,
            }
        )
    unique = {
        (row["ticker"], row["source"], row["record_hash"]): row for row in snapshots
    }
    if not unique:
        return 0
    statement = insert(SecurityReferenceHistory).values(list(unique.values()))
    statement = statement.on_conflict_do_nothing(
        constraint="uq_security_history_record"
    )
    result = session.execute(statement)
    return result.rowcount or 0


def _price_payload(row: DailyPriceBar | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        return {
            key: row.get(key)
            for key in (
                "ticker",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "vwap",
                "transactions",
                "adjusted",
                "source",
                "source_timestamp_ms",
            )
        }
    return {
        "ticker": row.ticker,
        "trade_date": row.trade_date,
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        "vwap": row.vwap,
        "transactions": row.transactions,
        "adjusted": row.adjusted,
        "source": row.source,
        "source_timestamp_ms": row.source_timestamp_ms,
    }


def _changed_price_payloads(
    existing_rows: Iterable[DailyPriceBar],
    incoming_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_by_key = {
        (row.ticker, row.trade_date): _price_payload(row) for row in existing_rows
    }
    changed: list[dict[str, Any]] = []
    for incoming_row in incoming_rows:
        incoming = _price_payload(incoming_row)
        key = (str(incoming["ticker"]), incoming["trade_date"])
        existing = existing_by_key.get(key)
        if existing is None:
            continue
        existing_source = str(existing.get("source") or "massive")
        incoming_source = str(incoming.get("source") or "massive")
        existing = {**existing, "source": existing_source}
        incoming = {**incoming, "source": incoming_source}
        if record_hash(existing) != record_hash(incoming):
            changed.extend((existing, incoming))
    return changed


def record_price_revisions(
    session: Session,
    incoming_rows: list[dict[str, Any]],
) -> int:
    if not incoming_rows:
        return 0
    keys = [(str(row["ticker"]), row["trade_date"]) for row in incoming_rows]
    existing = session.scalars(
        select(DailyPriceBar).where(
            tuple_(DailyPriceBar.ticker, DailyPriceBar.trade_date).in_(keys)
        )
    ).all()
    payloads = _changed_price_payloads(existing, incoming_rows)
    if not payloads:
        return 0

    revisions: list[dict[str, Any]] = []
    for payload in payloads:
        source = str(payload.get("source") or "massive")
        hashed_payload = {**payload, "source": source}
        revisions.append(
            {
                **hashed_payload,
                "record_hash": record_hash(hashed_payload),
            }
        )
    unique = {
        (
            row["ticker"],
            row["trade_date"],
            row["source"],
            row["record_hash"],
        ): row
        for row in revisions
    }
    statement = insert(DailyPriceBarRevision).values(list(unique.values()))
    statement = statement.on_conflict_do_nothing(
        constraint="uq_daily_price_revision_record"
    )
    result = session.execute(statement)
    return result.rowcount or 0
