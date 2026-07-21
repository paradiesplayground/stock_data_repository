from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.feature_calculation import (
    _latest_quarter_pair,
    _latest_by_period,
    _percent_change,
    _price_metrics,
    _rsi,
    _ttm_value,
)


def _fact(
    value: str,
    start: date | None,
    end: date,
    filed: date,
    fiscal_year: int,
    fiscal_period: str,
):
    return SimpleNamespace(
        value=Decimal(value),
        period_start=start,
        period_end=end,
        filed_date=filed,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        accession_number=f"{fiscal_year}-{fiscal_period}-{end}",
        form="10-K" if fiscal_period == "FY" else "10-Q",
        unit="USD",
    )


def test_percent_change_uses_percentage_points() -> None:
    assert _percent_change(Decimal("120"), Decimal("100")) == Decimal("20.0")
    assert _percent_change(Decimal("80"), Decimal("100")) == Decimal("-20.0")
    assert _percent_change(Decimal("1"), Decimal("0")) is None


def test_ttm_uses_annual_plus_current_ytd_minus_prior_ytd() -> None:
    facts = [
        _fact(
            "100",
            date(2025, 1, 1),
            date(2025, 12, 31),
            date(2026, 2, 15),
            2025,
            "FY",
        ),
        _fact(
            "30",
            date(2026, 1, 1),
            date(2026, 3, 31),
            date(2026, 5, 1),
            2026,
            "Q1",
        ),
        _fact(
            "20",
            date(2025, 1, 1),
            date(2025, 3, 31),
            date(2025, 5, 1),
            2025,
            "Q1",
        ),
    ]

    value, period_end, status = _ttm_value(facts, date(2026, 7, 17))

    assert value == Decimal("110")
    assert period_end == date(2026, 3, 31)
    assert status == "annual_plus_ytd"


def test_latest_quarter_finds_prior_year_comparison() -> None:
    facts = [
        _fact(
            "30",
            date(2026, 1, 1),
            date(2026, 3, 31),
            date(2026, 5, 1),
            2026,
            "Q1",
        ),
        _fact(
            "20",
            date(2025, 1, 1),
            date(2025, 3, 31),
            date(2025, 5, 1),
            2025,
            "Q1",
        ),
    ]

    current, prior, period_end = _latest_quarter_pair(facts, date(2026, 7, 17))

    assert current == Decimal("30")
    assert prior == Decimal("20")
    assert period_end == date(2026, 3, 31)


def test_comparative_facts_dedupe_by_period_dates_not_new_fiscal_metadata() -> None:
    original = _fact(
        "20",
        date(2025, 1, 1),
        date(2025, 3, 31),
        date(2025, 5, 1),
        2025,
        "Q1",
    )
    repeated = _fact(
        "21",
        date(2025, 1, 1),
        date(2025, 3, 31),
        date(2026, 5, 1),
        2026,
        "Q2",
    )

    selected = _latest_by_period([original, repeated])

    assert selected == [repeated]


def test_price_metrics_calculate_liquidity_and_drawdowns() -> None:
    as_of = date(2026, 7, 17)
    rows = []
    for offset in range(260):
        trade_date = as_of - timedelta(days=259 - offset)
        close = Decimal("200") if offset == 100 else Decimal(str(100 + offset / 10))
        rows.append(
            SimpleNamespace(
                trade_date=trade_date,
                close=close,
                high=close,
                volume=Decimal("1000000") + Decimal(offset),
            )
        )

    metrics, flags = _price_metrics(rows, as_of)

    assert metrics["price_date"] == as_of
    assert metrics["avg_volume_20d"] is not None
    assert metrics["avg_dollar_volume_20d"] is not None
    assert metrics["ema_10"] is not None
    assert metrics["ema_20"] is not None
    assert metrics["rsi_14"] is not None
    assert metrics["drawdown_52w_pct"] < 0
    assert "stale_price" not in flags


def test_rsi_handles_flat_series_without_division_by_zero() -> None:
    assert _rsi([Decimal("10")] * 20) == Decimal("50")
