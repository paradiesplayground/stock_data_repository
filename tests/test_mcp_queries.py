from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.mcp_queries import (
    _date_value,
    _json_value,
    _limit,
    get_data_freshness,
    query_security_features,
)


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
    assert "security_daily_features.price_date" in sql
    assert "security_daily_features.revenue_ttm_yoy_pct" in sql
    assert "securities.sic_code" in sql
    assert "securities.sic_code IS NULL" in sql


def test_feature_query_uses_latest_snapshot_on_or_before_requested_date() -> None:
    class EmptyResult:
        def all(self):
            return []

    class RecordingSession:
        date_statement = None

        def scalar(self, statement):
            self.date_statement = statement
            return date(2026, 7, 16)

        def execute(self, statement):
            return EmptyResult()

    session = RecordingSession()
    result = query_security_features(session, as_of_date="2026-07-17")

    assert result["as_of_date"] == "2026-07-16"
    assert "security_daily_features.as_of_date <=" in str(session.date_statement)


def test_freshness_reports_expected_session_and_screening_readiness() -> None:
    class Results:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    run = SimpleNamespace(
        job_name="derived_features",
        source="massive+sec-edgar",
        status="succeeded",
        started_at_utc=datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc),
        completed_at_utc=datetime(2026, 7, 20, 22, 5, tzinfo=timezone.utc),
        records_seen=10000,
        records_written=9000,
        details={"as_of_date": "2026-07-20"},
        error_message=None,
    )

    class FreshnessSession:
        def __init__(self):
            self.scalar_values = iter(
                [date(2026, 7, 20), date(2026, 7, 19), date(2026, 7, 20), run]
            )
            self.scalars_calls = 0

        def scalar(self, _statement):
            return next(self.scalar_values)

        def scalars(self, _statement):
            self.scalars_calls += 1
            return Results(["derived_features"] if self.scalars_calls == 1 else [])

    settings = Settings(
        massive_market_lag_days=0,
        timezone="America/Chicago",
    )
    result = get_data_freshness(
        FreshnessSession(),
        settings,
        datetime(2026, 7, 20, 17, 30, tzinfo=timezone.utc),
    )

    assert result["expected_market_date"] == "2026-07-20"
    assert result["market_is_current"] is True
    assert result["features_are_current"] is True
    assert result["ready_for_screening"] is True
    assert result["schedules"]["market_lag_days"] == 0
