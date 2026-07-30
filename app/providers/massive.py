import logging
import time
from datetime import date
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class MassiveClient:
    def __init__(self, settings: Settings):
        if not settings.massive_api_key:
            raise ValueError("MASSIVE_API_KEY is required")
        self.base_url = settings.massive_base_url.rstrip("/")
        self.min_interval = 60.0 / settings.massive_requests_per_minute
        self._last_request = 0.0
        self.client = httpx.Client(
            timeout=httpx.Timeout(120.0, connect=20.0),
            headers={"Authorization": f"Bearer {settings.massive_api_key}"},
            trust_env=False,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "MassiveClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(1, 7):
            self._throttle()
            response = self.client.get(url, params=params)
            self._last_request = time.monotonic()
            if response.status_code == 429:
                retry_after = float(
                    response.headers.get("Retry-After", min(60, 2**attempt))
                )
                logger.warning(
                    "Massive rate limit reached; retrying in %.1fs", retry_after
                )
                time.sleep(retry_after)
                continue
            if response.status_code >= 500 and attempt < 6:
                time.sleep(min(30, 2**attempt))
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("Massive request failed after retries")

    def _iter_results(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        while url:
            payload = self._get(url, params=params)
            yield from payload.get("results", [])
            url = payload.get("next_url") or ""
            params = None

    def iter_stock_tickers(
        self,
        active: bool = True,
        as_of_date: date | None = None,
    ) -> Iterator[dict[str, Any]]:
        url = f"{self.base_url}/v3/reference/tickers"
        params: dict[str, Any] = {
            "market": "stocks",
            "active": "true" if active else "false",
            "limit": 1000,
            "sort": "ticker",
            "order": "asc",
        }
        if as_of_date is not None:
            params["date"] = as_of_date.isoformat()
        yield from self._iter_results(url, params)

    def iter_active_stock_tickers(self) -> Iterator[dict[str, Any]]:
        """Backward-compatible active-only reference iterator."""
        yield from self.iter_stock_tickers(active=True)

    def get_grouped_daily(
        self,
        trade_date: date,
        *,
        adjusted: bool = True,
    ) -> dict[str, Any]:
        return self._get(
            f"{self.base_url}/v2/aggs/grouped/locale/us/market/stocks/{trade_date.isoformat()}",
            params={
                "adjusted": "true" if adjusted else "false",
                "include_otc": "false",
            },
        )

    def iter_splits(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": 5000,
            "sort": "execution_date.asc",
        }
        if start_date is not None:
            params["execution_date.gte"] = start_date.isoformat()
        if end_date is not None:
            params["execution_date.lte"] = end_date.isoformat()
        yield from self._iter_results(f"{self.base_url}/stocks/v1/splits", params)

    def iter_dividends(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": 5000,
            "sort": "ex_dividend_date.asc",
        }
        if start_date is not None:
            params["ex_dividend_date.gte"] = start_date.isoformat()
        if end_date is not None:
            params["ex_dividend_date.lte"] = end_date.isoformat()
        yield from self._iter_results(f"{self.base_url}/stocks/v1/dividends", params)

    def get_ticker_events(self, identifier: str) -> dict[str, Any]:
        encoded_identifier = quote(identifier, safe="")
        try:
            return self._get(
                f"{self.base_url}/vX/reference/tickers/{encoded_identifier}/events",
                params={"types": "ticker_change"},
            )
        except httpx.HTTPStatusError as error:
            # Massive's experimental ticker-events endpoint returns 404 when a
            # valid security has no event timeline. Treat that as an empty
            # result so one inactive ticker cannot abort the universe sync.
            if error.response.status_code == 404:
                return {"results": {"events": []}}
            raise
