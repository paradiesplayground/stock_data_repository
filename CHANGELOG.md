# Changelog

## 0.2.0

- Add a stateless Streamable HTTP MCP service on host port 8788.
- Expose six read-only tools for security search/lookup, price history, SEC facts, filings, and
  ingestion freshness.
- Keep screening, scoring, ranking, recommendations, sizing, and trading outside the data repo.

## 0.1.6

- Add a durable SEC daily-index checkpoint.
- Process only new SEC index files plus a configurable two-index safety overlap.
- Discover published index dates from SEC quarter directories so weekends and holidays do not
  appear as failures.
- Expose ingestion checkpoint state through `/v1/freshness`.

## 0.1.5

- Fix the SEC filing upsert for the `items` column, whose name collides with
  SQLAlchemy's `ColumnCollection.items()` method.

## 0.1.4

- Replace scheduled nightly SEC bulk downloads with incremental daily-index refreshes.
- Fetch submissions and company facts only for recently changed known CIKs.
- Keep `sync-sec` as the manual initial bootstrap/reconciliation command.

## 0.1.3

- Stop default historical backfills at yesterday rather than requesting an incomplete current-day summary.
- Reuse one Massive client across the backfill so request pacing applies between dates.

## 0.1.2

- Deduplicate Massive grouped daily bars before ticker/date upserts.
- Deduplicate SEC fact and filing batches defensively.
- Version the application image so Compose reliably rebuilds updated source.

## 0.1.1

- Deduplicate Massive ticker-reference rows before PostgreSQL upserts.
- Hide bound SQL parameters from error logs.

## 0.1.0

- Initial Massive and SEC EDGAR ingestion repository.
