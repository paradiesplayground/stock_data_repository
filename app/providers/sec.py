import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

DAILY_MASTER_PATTERN = re.compile(r"^master\.(\d{8})\.idx$")


def _parse_daily_master_dates(payload: dict[str, Any]) -> set[date]:
    dates: set[date] = set()
    for item in payload.get("directory", {}).get("item", []):
        match = DAILY_MASTER_PATTERN.match(str(item.get("name", "")))
        if not match:
            continue
        try:
            dates.add(date.fromisoformat(f"{match[1][:4]}-{match[1][4:6]}-{match[1][6:]}"))
        except ValueError:
            continue
    return dates


class SecClient:
    COMPANY_FACTS_PATH = "/Archives/edgar/daily-index/xbrl/companyfacts.zip"
    SUBMISSIONS_PATH = "/Archives/edgar/daily-index/bulkdata/submissions.zip"

    def __init__(self, settings: Settings):
        if not settings.sec_user_agent or "@" not in settings.sec_user_agent:
            raise ValueError("SEC_USER_AGENT must identify the application and include a contact email")
        self.settings = settings
        self.min_interval = 1.0 / settings.sec_requests_per_second
        self._last_request = 0.0
        self.client = httpx.Client(
            timeout=httpx.Timeout(900.0, connect=30.0),
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "SecClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def download(self, path: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        url = f"{self.settings.sec_base_url.rstrip('/')}{path}"
        for attempt in range(1, 6):
            self._throttle()
            try:
                with self.client.stream("GET", url) as response:
                    self._last_request = time.monotonic()
                    if response.status_code in {429, 503}:
                        retry_after = float(response.headers.get("Retry-After", min(60, 2**attempt)))
                        time.sleep(retry_after)
                        continue
                    response.raise_for_status()
                    with partial.open("wb") as output:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            output.write(chunk)
                partial.replace(destination)
                return destination
            except (httpx.HTTPError, OSError):
                partial.unlink(missing_ok=True)
                if attempt == 5:
                    raise
                time.sleep(min(30, 2**attempt))
        raise RuntimeError(f"SEC download failed: {url}")

    def download_companyfacts(self, destination: Path) -> Path:
        return self.download(self.COMPANY_FACTS_PATH, destination)

    def download_submissions(self, destination: Path) -> Path:
        return self.download(self.SUBMISSIONS_PATH, destination)

    def _get(
        self,
        url: str,
        missing_statuses: frozenset[int] = frozenset({404}),
    ) -> httpx.Response | None:
        for attempt in range(1, 6):
            self._throttle()
            response = self.client.get(url)
            self._last_request = time.monotonic()
            if response.status_code in missing_statuses:
                return None
            if response.status_code in {429, 503}:
                retry_after = float(response.headers.get("Retry-After", min(60, 2**attempt)))
                logger.warning("SEC request throttled; retrying in %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            if response.status_code >= 500 and attempt < 5:
                time.sleep(min(30, 2**attempt))
                continue
            response.raise_for_status()
            return response
        raise RuntimeError(f"SEC request failed after retries: {url}")

    def get_json(self, url: str) -> dict[str, Any] | None:
        response = self._get(url)
        return response.json() if response is not None else None

    def get_submissions(self, cik: str) -> dict[str, Any] | None:
        return self.get_json(
            f"{self.settings.sec_data_base_url.rstrip('/')}/submissions/CIK{cik.zfill(10)}.json"
        )

    def get_companyfacts(self, cik: str) -> dict[str, Any] | None:
        return self.get_json(
            f"{self.settings.sec_data_base_url.rstrip('/')}/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
        )

    def get_daily_master_index(self, index_date: date) -> str | None:
        quarter = ((index_date.month - 1) // 3) + 1
        url = (
            f"{self.settings.sec_base_url.rstrip('/')}/Archives/edgar/daily-index/"
            f"{index_date.year}/QTR{quarter}/master.{index_date:%Y%m%d}.idx"
        )
        response = self._get(url)
        return response.text if response is not None else None

    def get_daily_master_index_dates(self, year: int, quarter: int) -> set[date] | None:
        url = (
            f"{self.settings.sec_base_url.rstrip('/')}/Archives/edgar/daily-index/"
            f"{year}/QTR{quarter}/index.json"
        )
        payload = self.get_json(url)
        return _parse_daily_master_dates(payload) if payload is not None else None
