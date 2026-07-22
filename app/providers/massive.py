import logging
import time
from datetime import date
from typing import Any, Iterator

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

    def iter_stock_tickers(self, active: bool = True) -> Iterator[dict[str, Any]]:
        url = f"{self.base_url}/v3/reference/tickers"
        params: dict[str, Any] | None = {
            "market": "stocks",
            "active": "true" if active else "false",
            "limit": 1000,
            "sort": "ticker",
            "order": "asc",
        }
        while url:
            payload = self._get(url, params=params)
            yield from payload.get("results", [])
            next_url = payload.get("next_url")
            url = next_url or ""
            params = None

    def iter_active_stock_tickers(self) -> Iterator[dict[str, Any]]:
        """Backward-compatible active-only reference iterator."""
        yield from self.iter_stock_tickers(active=True)

    def get_grouped_daily(self, trade_date: date) -> dict[str, Any]:
        return self._get(
            f"{self.base_url}/v2/aggs/grouped/locale/us/market/stocks/{trade_date.isoformat()}",
            params={"adjusted": "true", "include_otc": "false"},
        )
