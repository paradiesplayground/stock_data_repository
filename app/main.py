from fastapi import FastAPI
from sqlalchemy import text

from app.api import router
from app.db import engine
from app.logging_config import configure_logging

configure_logging()

app = FastAPI(
    title="Stock Data Repository",
    version="0.3.2",
    description=(
        "Read-only repository of Massive market data, SEC EDGAR facts/filings, "
        "and deterministic derived features."
    ),
)
app.include_router(router)


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["operations"])
def ready() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}
