from datetime import date, timedelta
from decimal import Decimal

from app.services.alert_outcomes import Bar, forward_window, trade_path

START = date(2026, 10, 1)


def _bars(*rows: tuple[str, str, str, str]) -> list[Bar]:
    return [
        Bar(START + timedelta(days=index), *(Decimal(value) for value in row))
        for index, row in enumerate(rows)
    ]


def _flat(count: int, price: str = "100") -> list[tuple[str, str, str, str]]:
    return [(price, price, price, price)] * count


def test_trade_reaches_2r_target() -> None:
    bars = _bars(("100", "101", "99", "100"), ("100", "106", "100", "105"), ("105", "121", "104", "120"))

    status, metrics = trade_path(bars, Decimal("102"), Decimal("96"))

    assert status == "target_2r"
    assert metrics["entry_date"] == (START + timedelta(days=1)).isoformat()
    assert metrics["entry_price"] == "102.0000"
    assert metrics["first_1r_date"] == (START + timedelta(days=2)).isoformat()
    assert metrics["exit_r"] == "2.0000"


def test_gap_above_trigger_fills_at_open_and_gap_below_stop_fills_at_open() -> None:
    bars = _bars(("104", "105", "103", "104"), ("90", "91", "88", "89"))

    status, metrics = trade_path(bars, Decimal("102"), Decimal("96"))

    assert status == "stopped"
    assert metrics["entry_price"] == "104.0000"
    assert metrics["exit_price"] == "90.0000"
    assert Decimal(metrics["exit_r"]) == (Decimal("90") - Decimal("104")) / Decimal("8")


def test_same_session_stop_and_target_counts_as_stop() -> None:
    bars = _bars(("100", "103", "99", "102"), ("102", "130", "90", "100"))

    status, metrics = trade_path(bars, Decimal("102"), Decimal("96"))

    assert status == "stopped"
    assert metrics["exit_r"] == "-1.0000"
    assert metrics["first_2r_date"] is None


def test_untriggered_trade_is_pending_until_window_closes() -> None:
    assert trade_path(_bars(*_flat(5)), Decimal("102"), Decimal("96"))[0] == "pending"
    assert trade_path(_bars(*_flat(20)), Decimal("102"), Decimal("96"))[0] == "not_triggered"


def test_open_trade_expires_at_hold_limit() -> None:
    rows = [("100", "103", "99", "102")] + [("102", "104", "101", "103")] * 25
    status, metrics = trade_path(_bars(*rows), Decimal("102"), Decimal("96"))

    assert status == "expired"
    assert metrics["sessions_held"] == 20
    assert metrics["exit_r"] == "0.1667"


def test_missing_or_inverted_plan_is_not_traded() -> None:
    assert trade_path(_bars(*_flat(3)), None, Decimal("96"))[0] == "no_trade_plan"
    assert trade_path(_bars(*_flat(3)), Decimal("96"), Decimal("102"))[0] == "no_trade_plan"


def test_forward_window_reports_return_excess_and_excursions() -> None:
    bars = _bars(("100", "112", "95", "110"), ("110", "111", "104", "105"))
    benchmark = _bars(("400", "402", "399", "402"), ("402", "405", "400", "404"))

    window = forward_window(bars, Decimal("100"), 2, benchmark, Decimal("400"))

    assert window["close_return_pct"] == "5.0000"
    assert window["benchmark_return_pct"] == "1.0000"
    assert window["excess_return_pct"] == "4.0000"
    assert window["max_high_pct"] == "12.0000"
    assert window["min_low_pct"] == "-5.0000"
    assert forward_window(bars, Decimal("100"), 5) is None
