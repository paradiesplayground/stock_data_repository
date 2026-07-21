from datetime import date

import httpx
import respx

from app.config import Settings
from app.providers.massive import MassiveClient
from app.services.massive_ingestion import (
    _dedupe_price_rows,
    _dedupe_security_rows,
    latest_eligible_market_date,
    market_dates_to_sync,
    market_target_date,
)


@respx.mock
def test_grouped_daily_uses_bulk_market_endpoint_and_bearer_auth() -> None:
    route = respx.get(
        "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/2026-07-17",
        params={"adjusted": "true", "include_otc": "false"},
    ).mock(return_value=httpx.Response(200, json={"status": "OK", "results": []}))
    settings = Settings(
        massive_api_key="secret",
        massive_requests_per_minute=10000,
        sec_user_agent="Test test@example.com",
    )

    with MassiveClient(settings) as client:
        payload = client.get_grouped_daily(date(2026, 7, 17))

    assert payload["status"] == "OK"
    assert route.called
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret"


def test_duplicate_tickers_are_removed_before_upsert() -> None:
    rows = [
        {"ticker": "TEST", "name": "Older name"},
        {"ticker": "OTHER", "name": "Other company"},
        {"ticker": "TEST", "name": "Current name"},
    ]

    deduplicated = _dedupe_security_rows(rows)

    assert len(deduplicated) == 2
    assert (
        next(row for row in deduplicated if row["ticker"] == "TEST")["name"]
        == "Current name"
    )


def test_duplicate_daily_bars_are_removed_before_upsert() -> None:
    rows = [
        {"ticker": "TEST", "close": 10},
        {"ticker": "OTHER", "close": 20},
        {"ticker": "TEST", "close": 11},
    ]

    deduplicated = _dedupe_price_rows(rows)

    assert len(deduplicated) == 2
    assert next(row for row in deduplicated if row["ticker"] == "TEST")["close"] == 11


def test_latest_eligible_market_date_skips_weekends() -> None:
    assert latest_eligible_market_date(date(2026, 7, 20)) == date(2026, 7, 17)
    assert latest_eligible_market_date(date(2026, 7, 19)) == date(2026, 7, 17)
    assert latest_eligible_market_date(date(2026, 7, 18)) == date(2026, 7, 17)


def test_market_target_date_supports_same_day_and_lagged_workflows() -> None:
    assert market_target_date(date(2026, 7, 20), 0) == date(2026, 7, 20)
    assert market_target_date(date(2026, 7, 19), 0) == date(2026, 7, 17)
    assert market_target_date(date(2026, 7, 20), 1) == date(2026, 7, 17)
    assert market_target_date(date(2026, 7, 21), 1) == date(2026, 7, 20)


def test_incremental_market_dates_catch_up_missing_weekdays() -> None:
    assert market_dates_to_sync(date(2026, 7, 15), date(2026, 7, 20)) == [
        date(2026, 7, 16),
        date(2026, 7, 17),
        date(2026, 7, 20),
    ]
    assert market_dates_to_sync(date(2026, 7, 17), date(2026, 7, 17)) == []
