from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.mcp_queries import _date_value, _json_value, _limit


def test_mcp_json_values_preserve_exact_numbers_and_iso_dates() -> None:
    assert _json_value(Decimal("123.45000000")) == "123.45000000"
    assert _json_value(date(2026, 7, 17)) == "2026-07-17"
    assert _json_value(datetime(2026, 7, 17, 12, 30, tzinfo=timezone.utc)) == (
        "2026-07-17T12:30:00+00:00"
    )


def test_mcp_date_validation_is_explicit() -> None:
    assert _date_value("2026-07-17", "start_date") == date(2026, 7, 17)
    with pytest.raises(ValueError, match="start_date must be YYYY-MM-DD"):
        _date_value("07/17/2026", "start_date")


def test_mcp_limits_are_bounded() -> None:
    assert _limit(100, 500) == 100
    with pytest.raises(ValueError, match="between 1 and 500"):
        _limit(501, 500)
