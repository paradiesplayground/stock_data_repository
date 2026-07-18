from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://stockdata:stockdata@postgres:5432/stockdata"

    massive_api_key: str = ""
    massive_base_url: str = "https://api.massive.com"
    massive_requests_per_minute: int = Field(default=5, ge=1, le=10000)
    massive_backfill_days: int = Field(default=400, ge=30, le=5000)

    sec_user_agent: str = ""
    sec_base_url: str = "https://www.sec.gov"
    sec_data_base_url: str = "https://data.sec.gov"
    sec_requests_per_second: int = Field(default=5, ge=1, le=10)
    sec_keep_archives: bool = True
    sec_incremental_lookback_days: int = Field(default=7, ge=1, le=31)
    sec_incremental_overlap_indexes: int = Field(default=2, ge=0, le=10)

    data_dir: Path = Path("/data")
    api_bearer_token: str = ""

    mcp_host: str = "0.0.0.0"
    mcp_port: int = Field(default=8001, ge=1, le=65535)

    timezone: str = "America/Chicago"
    market_sync_cron: str = "20 15 * * 1-5"
    reference_sync_cron: str = "30 2 * * 1-5"
    sec_sync_cron: str = "30 4 * * 1-6"
    log_level: str = "INFO"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
