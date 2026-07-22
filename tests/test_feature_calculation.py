from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import app.services.feature_calculation as feature_calculation
from app.config import Settings
from app.services.feature_calculation import (
    _feature_universe_statement,
    _financial_metrics,
    _latest_instant,
    _latest_quarter_pair,
    _latest_by_period,
    _percent_change,
    _price_metrics,
    _resolve_feature_securities,
    _rsi,
    _ttm_value,
)


def _security(**overrides):
    values = {
        "ticker": "CASI",
        "name": "CASI Pharmaceuticals, Inc.",
        "market": "stocks",
        "locale": "us",
        "currency": "usd",
        "primary_exchange": "XNAS",
        "security_type": "CS",
        "active": False,
        "cik": "0000896156",
        "composite_figi": "BBG000TEST01",
        "share_class_figi": "BBG001TEST01",
        "sic_code": "2834",
        "sic_description": "Pharmaceutical Preparations",
        "fiscal_year_end": "1231",
        "state_of_incorporation": "KY",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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


def test_historical_universe_is_driven_by_exact_date_price_bar() -> None:
    sql = str(_feature_universe_statement(date(2026, 1, 21)))

    assert "JOIN daily_price_bars" in sql
    assert "daily_price_bars.trade_date" in sql
    assert "WHERE securities.active" not in sql


def test_currently_inactive_security_remains_in_historical_universe() -> None:
    resolved = _resolve_feature_securities([_security()], [])

    assert len(resolved) == 1
    assert resolved[0].ticker == "CASI"
    assert resolved[0].active is True
    assert resolved[0].current_active is False
    assert resolved[0].reference_metadata_imputed is True


def test_reference_snapshot_overrides_later_current_metadata() -> None:
    historical_values = {
        key: value
        for key, value in vars(_security(active=True)).items()
        if key != "ticker"
    }
    history = SimpleNamespace(
        ticker="CASI",
        snapshot={"ticker": "CASI", **historical_values},
        observed_at_utc=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )
    current = _security(name="Later company name", primary_exchange="OTCM")

    resolved = _resolve_feature_securities([current], [history])

    assert resolved[0].name == "CASI Pharmaceuticals, Inc."
    assert resolved[0].primary_exchange == "XNAS"
    assert resolved[0].reference_metadata_imputed is False
    assert resolved[0].reference_observed_at_utc == history.observed_at_utc


def test_non_common_stock_is_excluded_from_feature_universe() -> None:
    assert _resolve_feature_securities([_security(security_type="ETF")], []) == []


def test_resumable_backfill_skips_only_complete_dates(monkeypatch) -> None:
    first = date(2026, 1, 20)
    second = date(2026, 1, 21)

    class ScalarRows:
        def all(self):
            return [first, second]

    class Session:
        def scalars(self, _statement):
            return ScalarRows()

    calculated = []
    monkeypatch.setattr(
        feature_calculation,
        "_feature_date_is_complete",
        lambda _session, market_date: market_date == first,
    )

    def calculate(_session, _settings, market_date):
        calculated.append(market_date)
        return 1, 1

    monkeypatch.setattr(feature_calculation, "calculate_daily_features", calculate)

    result = feature_calculation.backfill_daily_features(
        Session(),
        Settings(),
        first,
        second,
        resume=True,
    )

    assert calculated == [second]
    assert result["completed_dates"] == [second.isoformat()]
    assert result["skipped_dates"] == [first.isoformat()]


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
                open=close,
                close=close,
                high=close,
                low=close - Decimal("1"),
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
    assert metrics["drawdown_12w_high_pct"] <= 0
    assert metrics["drawdown_52w_pct"] < 0
    assert metrics["atr_14"] is not None
    assert metrics["high_20d"] is not None
    assert metrics["low_60d"] is not None
    assert "stale_price" not in flags


def test_average_dollar_volume_uses_each_days_price() -> None:
    as_of = date(2026, 7, 17)
    rows = [
        SimpleNamespace(
            trade_date=as_of - timedelta(days=1),
            open=Decimal("10"),
            close=Decimal("10"),
            high=Decimal("10"),
            low=Decimal("10"),
            volume=Decimal("100"),
        ),
        SimpleNamespace(
            trade_date=as_of,
            open=Decimal("20"),
            close=Decimal("20"),
            high=Decimal("20"),
            low=Decimal("20"),
            volume=Decimal("200"),
        ),
    ]

    metrics, _ = _price_metrics(rows, as_of)

    assert metrics["avg_dollar_volume_20d"] == Decimal("2500")
    assert metrics["daily_return_pct"] == Decimal("100")


def test_rsi_handles_flat_series_without_division_by_zero() -> None:
    assert _rsi([Decimal("10")] * 20) == Decimal("50")


def test_latest_instant_uses_freshest_period_across_aliases() -> None:
    facts = {
        "CashAndCashEquivalentsAtCarryingValue": [
            _fact("10", None, date(2025, 12, 31), date(2026, 2, 1), 2025, "FY")
        ],
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": [
            _fact("12", None, date(2026, 3, 31), date(2026, 5, 1), 2026, "Q1")
        ],
    }

    value, period_end = _latest_instant(
        facts,
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        date(2026, 7, 17),
    )

    assert value == Decimal("12")
    assert period_end == date(2026, 3, 31)


def test_financial_metrics_include_marketable_securities_and_commercial_paper() -> None:
    period_end = date(2026, 3, 28)
    filed = date(2026, 5, 1)
    facts = {
        "CashAndCashEquivalentsAtCarryingValue": [
            _fact("45.572", None, period_end, filed, 2026, "Q2")
        ],
        "MarketableSecuritiesCurrent": [
            _fact("22.935", None, period_end, filed, 2026, "Q2")
        ],
        "LongTermDebtCurrent": [_fact("8.310", None, period_end, filed, 2026, "Q2")],
        "LongTermDebtNoncurrent": [
            _fact("74.404", None, period_end, filed, 2026, "Q2")
        ],
        "CommercialPaper": [_fact("1.997", None, period_end, filed, 2026, "Q2")],
    }

    metrics, _ = _financial_metrics(facts, date(2026, 7, 17), Decimal("333.74"))

    assert metrics["cash_and_short_term_investments"] == Decimal("68.507")
    assert metrics["total_debt"] == Decimal("84.711")


def test_financial_metrics_sum_current_marketable_security_components() -> None:
    period_end = date(2026, 4, 26)
    filed = date(2026, 5, 20)
    facts = {
        "CashAndCashEquivalentsAtCarryingValue": [
            _fact("13.237", None, period_end, filed, 2027, "Q1")
        ],
        "MarketableDebtSecuritiesCurrent": [
            _fact("37.098", None, period_end, filed, 2027, "Q1")
        ],
        "MarketableEquitySecuritiesCurrent": [
            _fact("30.237", None, period_end, filed, 2027, "Q1")
        ],
        "LongTermDebtCurrent": [_fact("1.000", None, period_end, filed, 2027, "Q1")],
        "LongTermDebtNoncurrent": [_fact("7.470", None, period_end, filed, 2027, "Q1")],
    }

    metrics, _ = _financial_metrics(facts, date(2026, 7, 17), Decimal("202.81"))

    assert metrics["cash_and_short_term_investments"] == Decimal("80.572")
    assert metrics["total_debt"] == Decimal("8.470")
