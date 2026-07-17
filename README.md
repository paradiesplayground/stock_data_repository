# Stock Data Repository

An authoritative, read-only data repository for U.S. equity research. It ingests:

- Massive reference data and adjusted daily OHLCV bars
- SEC EDGAR bulk company facts and filing metadata
- Source freshness and ingestion audit records

It deliberately does **not** screen, score, rank, recommend, size, or trade stocks. Those decisions belong to the downstream swing-trading skill. This service provides facts and their provenance; missing facts remain missing instead of being creatively hallucinated into existence.

## Architecture

The Compose project runs one application image in three roles:

- `migrate`: applies database migrations and exits
- `api`: read-only FastAPI repository on host port `8787`
- `worker`: scheduled Massive and SEC ingestion

PostgreSQL stores normalized data. Original SEC ZIP archives are retained under `/data/raw/sec` by default.

## Stored data

| Dataset | Source | Stored fields |
|---|---|---|
| Security reference | Massive + SEC | Ticker, exchange, type, CIK, FIGI, SIC/industry, active status |
| Daily prices | Massive | Adjusted OHLC, volume, VWAP, transactions, source timestamp |
| Financial facts | SEC EDGAR | Taxonomy, concept, unit, value, reporting period, form, filing date, accession |
| Filing metadata | SEC EDGAR | Form, dates, document, items, XBRL flags, canonical SEC URL |
| Freshness | Internal audit | Job, status, timestamps, counts, source request details, failures |

SEC financial values are stored as reported source facts. The repository does not resolve competing revenue tags, calculate TTM values, calculate growth, or choose a “best” filing context. That interpretation remains downstream and auditable.

## Prerequisites

1. A Massive Stocks API key. Massive currently includes the grouped daily market endpoint on all Stocks plans. Basic is end-of-day; Starter is 15-minute delayed.
2. A descriptive SEC User-Agent containing a monitored email address.
3. An existing Unraid Docker network named `paradiesplayground`.

## Unraid installation

Copy this directory to:

```text
/mnt/user/appdata/stock-data-repository/compose
```

Ensure the application data directory is writable by Unraid's `nobody:users` account:

```bash
mkdir -p /mnt/user/appdata/stock-data-repository/data
chown -R 99:100 /mnt/user/appdata/stock-data-repository/data
```

Then create the environment file:

```bash
cd /mnt/user/appdata/stock-data-repository/compose
cp .env.example .env
```

Edit `.env` and set at minimum:

```dotenv
POSTGRES_PASSWORD=a-long-random-password
DATABASE_URL=postgresql+psycopg://stockdata:THE_SAME_PASSWORD@postgres:5432/stockdata
MASSIVE_API_KEY=your-key-from-massive
SEC_USER_AGENT=StockDataRepository your-real-email@example.com
API_BEARER_TOKEN=another-long-random-token
```

Do not paste API keys into chat, commit them, or bake them into the image.

Start the project:

```bash
docker compose up -d --build
```

Check status:

```bash
docker compose ps
curl http://localhost:8787/health
curl http://localhost:8787/ready
```

## First ingestion

Populate ticker/CIK reference data first:

```bash
docker compose exec worker python -m app.cli sync-reference
```

Backfill approximately 400 calendar days of daily prices. This supplies enough history for 12-week movement and 52-week-high calculations downstream:

```bash
docker compose exec worker python -m app.cli backfill-market
```

Download and normalize SEC company facts and recent filing history:

```bash
docker compose exec worker python -m app.cli sync-sec
```

SEC bulk archives are large. The first SEC import and first price backfill can take a while; subsequent scheduled updates are incremental database upserts.

## Schedule

Defaults use `America/Chicago`:

| Job | Default | Reason |
|---|---:|---|
| Massive reference | 2:30 AM weekdays | Refresh ticker and CIK mappings |
| SEC incremental data | 4:30 AM Tuesday-Saturday | Refresh only CIKs found in recent SEC daily indexes |
| Massive daily bars | 3:20 PM weekdays | Allows the 15-minute delayed final market bar to become available |

Cron expressions are configurable in `.env`. Because Starter is 15 minutes delayed, a downstream daily screen should run after ingestion completes—roughly 3:25 PM Central, not exactly 3:15 PM. Physics and exchange licensing remain stubbornly uninterested in our preferred notification time.

## Read-only API

Health endpoints are public. If `API_BEARER_TOKEN` is configured, all `/v1` routes require it:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8787/v1/freshness
```

Available routes:

```text
GET /health
GET /ready
GET /v1/freshness
GET /v1/securities
GET /v1/securities/{ticker}
GET /v1/securities/{ticker}/prices
GET /v1/securities/{ticker}/facts
GET /v1/securities/{ticker}/filings
```

Interactive OpenAPI documentation is available at `http://UNRAID-IP:8787/docs`.

## Manual jobs

```bash
python -m app.cli sync-reference
python -m app.cli sync-market --date 2026-07-17
python -m app.cli backfill-market --start 2025-06-01 --end 2026-07-17
python -m app.cli sync-companyfacts
python -m app.cli sync-submissions
python -m app.cli sync-sec
python -m app.cli sync-sec-incremental
```

`sync-sec` is the initial bulk bootstrap and is not scheduled nightly. The worker uses
`sync-sec-incremental`, reviewing recent SEC daily indexes and refreshing only changed
companies. A manual bulk run can be used occasionally for reconciliation.

Each job writes a row to `ingestion_runs`, including failures. The `/v1/freshness` endpoint exposes the latest state so downstream tools can treat stale or missing data as unverified.

## Development and tests

```bash
python -m pip install -r requirements-dev.txt
ruff check app tests
pytest
```

## Planned next phase

The MCP server will wrap these read-only repository functions and expose compact tools such as security lookup, price history, reported facts, filing metadata, and freshness. It will not add screening logic.
