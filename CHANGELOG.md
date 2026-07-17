# Changelog

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
