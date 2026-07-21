from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.mcp_queries import _date_value, _json_value, _limit, query_security_features


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


def test_feature_query_builds_neutral_filtered_statement() -> None:
    class EmptyResult:
        def all(self):
            return []

    class RecordingSession:
        statement = None

        def scalar(self, statement):
            return date(2026, 7, 17)

        def execute(self, statement):
            self.statement = statement
            return EmptyResult()

    session = RecordingSession()
    result = query_security_features(
        session,
        min_price=5,
        min_ttm_revenue_growth_pct=40,
        max_price_change_12w_pct=-20,
        min_avg_dollar_volume_20d=30_000_000,
        exclude_healthcare=True,
    )

    assert result["as_of_date"] == "2026-07-17"
    assert result["count"] == 0
    sql = str(session.statement)
    assert "security_daily_features.close" in sql
    assert "security_daily_features.revenue_ttm_yoy_pct" in sql
    assert "securities.sic_code" in sql
