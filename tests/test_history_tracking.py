from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.history import _changed_price_payloads, _json_safe, record_hash
from app.services.strategy_tracking import (
    _canonical_hash,
    _datetime,
    _identifier,
    record_strategy_run,
)
from app.models import (
    StrategyCandidate,
    StrategyDefinition,
    StrategyEvidence,
    StrategyRun,
)


def test_history_hash_is_stable_across_key_order_and_exact_numeric_values() -> None:
    first = {"ticker": "AAPL", "close": Decimal("123.4500"), "active": True}
    second = {"active": True, "close": Decimal("123.4500"), "ticker": "AAPL"}

    assert record_hash(first) == record_hash(second)
    assert _json_safe(first)["close"] == "123.4500"


def test_strategy_payload_hash_is_stable_across_key_order() -> None:
    assert _canonical_hash({"a": 1, "b": [2, 3]}) == _canonical_hash(
        {"b": [2, 3], "a": 1}
    )


def test_price_history_retains_only_actual_revisions() -> None:
    existing = type(
        "Price",
        (),
        {
            "ticker": "AAPL",
            "trade_date": datetime(2026, 7, 17).date(),
            "open": Decimal("100"),
            "high": Decimal("105"),
            "low": Decimal("99"),
            "close": Decimal("104"),
            "volume": Decimal("1000000"),
            "vwap": Decimal("103"),
            "transactions": 5000,
            "adjusted": True,
            "source": "massive",
            "source_timestamp_ms": 1,
        },
    )()
    unchanged = _price_payload_dict(existing)
    new_ticker = {**unchanged, "ticker": "MSFT"}
    revised = {**unchanged, "close": Decimal("104.25")}

    assert _changed_price_payloads([existing], [unchanged, new_ticker]) == []
    changed = _changed_price_payloads([existing], [revised])
    assert len(changed) == 2
    assert changed[0]["close"] == Decimal("104")
    assert changed[1]["close"] == Decimal("104.25")


def _price_payload_dict(row) -> dict:
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


def test_strategy_identifiers_and_timestamps_are_strict() -> None:
    assert _identifier(" Fallen-Growth.V1 ", "strategy", 64) == "fallen-growth.v1"
    assert _datetime("2026-07-17T12:30:00Z", "cutoff") == datetime(
        2026, 7, 17, 12, 30, tzinfo=timezone.utc
    )
    with pytest.raises(ValueError, match="lowercase letters"):
        _identifier("not allowed!", "strategy", 64)


def test_complete_strategy_run_is_normalized_and_committed() -> None:
    class EmptySession:
        def __init__(self) -> None:
            self.added = []
            self.commits = 0

        def scalar(self, _statement):
            return None

        def add(self, item) -> None:
            self.added.append(item)
            if isinstance(item, StrategyDefinition):
                item.id = 1

        def flush(self) -> None:
            return None

        def commit(self) -> None:
            self.commits += 1

    session = EmptySession()
    result = record_strategy_run(
        session,
        strategy_key="Fallen-Growth-Swing",
        strategy_version="1.0.0",
        strategy_name="Fallen growth swing",
        as_of_date="2026-07-17",
        run_type="as_run",
        idempotency_key="fallen-growth-swing:1.0.0:2026-07-17:as-run",
        configuration={"min_revenue_growth_pct": 40},
        filters={"exclude_industry_groups": ["Healthcare"]},
        candidates=[
            {
                "ticker": "aapl",
                "stage": "New",
                "action": "Watch",
                "score": "7.25",
                "metrics": {"price": "210.00"},
                "reasons": ["newly passed liquidity threshold"],
            }
        ],
        evidence=[
            {
                "ticker": "AAPL",
                "evidence_type": "sec-filing",
                "accepted_at_utc": "2026-07-17T20:00:00Z",
                "accession_number": "0000000000-26-000001",
            }
        ],
        feature_calculation_version="1.2.0",
        data_cutoff_at_utc="2026-07-17T23:00:00Z",
    )

    assert result["recorded"] is True
    assert result["candidate_count"] == 1
    assert result["evidence_count"] == 1
    assert session.commits == 1
    definition = next(
        item for item in session.added if isinstance(item, StrategyDefinition)
    )
    run = next(item for item in session.added if isinstance(item, StrategyRun))
    candidate = next(
        item for item in session.added if isinstance(item, StrategyCandidate)
    )
    evidence = next(
        item for item in session.added if isinstance(item, StrategyEvidence)
    )
    assert definition.strategy_key == "fallen-growth-swing"
    assert definition.version == "1.0.0"
    assert run.as_of_date.isoformat() == "2026-07-17"
    assert candidate.ticker == "AAPL"
    assert candidate.score == Decimal("7.25")
    assert evidence.accepted_at_utc == datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
