from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.history import _changed_price_payloads, _json_safe, record_hash
from app.services.strategy_tracking import (
    _canonical_hash,
    _datetime,
    _identifier,
    get_strategy_run,
    record_strategy_run,
)
from app.models import (
    StrategyCandidate,
    StrategyDefinition,
    StrategyEvidence,
    StrategyRun,
)
from app.strategy_decisions import StrategyCandidateDecision


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


def test_get_strategy_run_orders_decision_status_before_score() -> None:
    run = type(
        "Run",
        (),
        {
            "run_id": "run-1",
            "as_of_date": datetime(2026, 8, 18).date(),
            "run_type": "as_run",
            "feature_calculation_version": "1.5.0",
            "data_cutoff_at_utc": None,
            "filters": {},
            "summary": {},
            "report_markdown": None,
            "payload_hash": "hash",
            "generated_at_utc": datetime(2026, 8, 18, tzinfo=timezone.utc),
        },
    )()
    definition = type(
        "Definition",
        (),
        {
            "strategy_key": "fallen-growth-swing",
            "version": "0.7.0",
            "name": "Fallen growth swing",
            "configuration": {},
            "skill_fingerprint": None,
        },
    )()
    candidates = [
        type(
            "Candidate",
            (),
            {
                "ticker": "WATCH",
                "stage": "watch",
                "action": "wait",
                "score": Decimal("99"),
                "score_components": None,
                "metrics": None,
                "reasons": None,
                "trade_plan": None,
                "payload": None,
            },
        )(),
        type(
            "Candidate",
            (),
            {
                "ticker": "BUY",
                "stage": "qualified",
                "action": "buy",
                "score": Decimal("80"),
                "score_components": None,
                "metrics": None,
                "reasons": None,
                "trade_plan": None,
                "payload": None,
            },
        )(),
    ]
    decisions = [
        type(
            "Decision",
            (),
            {
                "ticker": "WATCH",
                "decision_status": "WATCH",
                "status_reason": "Still developing",
                "next_condition": "Reclaim trigger",
                "current_entry": None,
                "pct_above_trigger": None,
                "t1_r": None,
                "t2_r": None,
                "technical_gate_passed": False,
                "market_regime_gate_passed": True,
            },
        )(),
        type(
            "Decision",
            (),
            {
                "ticker": "BUY",
                "decision_status": "BUY_SETUP",
                "status_reason": "All entry gates passed",
                "next_condition": "Enter only inside the stated buy zone",
                "current_entry": Decimal("10"),
                "pct_above_trigger": Decimal("2"),
                "t1_r": Decimal("1.2"),
                "t2_r": Decimal("2"),
                "technical_gate_passed": True,
                "market_regime_gate_passed": True,
            },
        )(),
    ]

    class RowResult:
        def one_or_none(self):
            return run, definition

    class ScalarResult:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class CapturingSession:
        def __init__(self) -> None:
            self.scalar_call = 0

        def execute(self, _statement):
            return RowResult()

        def scalars(self, _statement):
            self.scalar_call += 1
            rows = {1: candidates, 2: decisions, 3: [], 4: []}[self.scalar_call]
            return ScalarResult(rows)

    result = get_strategy_run(CapturingSession(), "run-1")

    assert [item["ticker"] for item in result["candidates"]] == ["BUY", "WATCH"]
    assert result["candidates"][0]["decision_status"] == "BUY_SETUP"
    assert result["candidates"][0]["status_reason"] == "All entry gates passed"


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
        strategy_version="0.7.0",
        strategy_name="Fallen growth swing",
        as_of_date="2026-08-18",
        run_type="as_run",
        decision_contract_version="0.7",
        idempotency_key="fallen-growth-swing:0.7.0:2026-08-18:as-run",
        configuration={"min_revenue_growth_pct": 40},
        filters={"exclude_industry_groups": ["Healthcare"]},
        summary={"buy_setup_count": 1},
        report_markdown="# Decision Summary / Best Setups\n\nAAPL is a BUY_SETUP.",
        candidates=[
            {
                "ticker": "aapl",
                "stage": "qualified",
                "screen_bucket": "qualified",
                "action": "buy",
                "score": "87.25",
                "metrics": {"price": "210.00"},
                "reasons": ["technical confirmation complete"],
                "decision_status": "BUY_SETUP",
                "status_reason": "All entry and risk gates passed.",
                "next_condition": "Enter only while price remains inside the planned buy zone.",
                "technical_state": "confirmed",
                "current_entry": "210.00",
                "pct_above_trigger": "3.25",
                "t1_r": "1.20",
                "t2_r": "2.10",
                "technical_gate_passed": True,
                "market_regime_gate_passed": True,
            }
        ],
        evidence=[
            {
                "ticker": "AAPL",
                "evidence_type": "sec-filing",
                "accepted_at_utc": "2026-08-18T20:00:00Z",
                "accession_number": "0000000000-26-000001",
            }
        ],
        feature_calculation_version="1.5.0",
        data_cutoff_at_utc="2026-08-18T23:00:00Z",
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
    decision = next(
        item for item in session.added if isinstance(item, StrategyCandidateDecision)
    )
    evidence = next(
        item for item in session.added if isinstance(item, StrategyEvidence)
    )
    assert definition.strategy_key == "fallen-growth-swing"
    assert definition.version == "0.7.0"
    assert run.as_of_date.isoformat() == "2026-08-18"
    assert run.summary == {"buy_setup_count": 1}
    assert "report_markdown" not in run.summary
    assert run.report_markdown.startswith("# Decision Summary / Best Setups")
    assert candidate.ticker == "AAPL"
    assert candidate.score == Decimal("87.25")
    assert decision.decision_status == "BUY_SETUP"
    assert decision.status_reason == "All entry and risk gates passed."
    assert decision.t1_r == Decimal("1.20")
    assert decision.t2_r == Decimal("2.10")
    assert decision.technical_gate_passed is True
    assert decision.market_regime_gate_passed is True
    assert evidence.accepted_at_utc == datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)
