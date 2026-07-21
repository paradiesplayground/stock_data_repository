# Changelog

## 0.3.1

- Make the scheduled market target configurable for same-day EOD or prior-session workflows.
- Retry temporarily unavailable same-day EOD responses with configurable bounded backoff.
- Reject partial grouped-daily payloads using absolute and prior-session coverage thresholds.
- Apply the absolute completeness check to explicit one-day market reloads as well.
- Prevent derived-feature jobs from succeeding against stale or materially incomplete prices.
- Expose the expected session, configured schedules, and screening-readiness booleans in freshness.
- Make feature queries select the latest snapshot on or before a requested date.
- Preserve securities with unknown SIC codes when callers exclude known healthcare companies.
- Calculate average dollar volume from each day's close and volume, and omit stale-price rows from
  snapshot-wide feature queries.
- Use the configured local timezone for SEC and market date boundaries.
- Share one freshness implementation between the HTTP API and MCP service.

## 0.3.0

- Add versioned daily derived fields without adding scores, rankings, or recommendations.
- Calculate price movement, 52-week drawdown, liquidity, EMA, RSI, and relative-volume fields.
- Conservatively normalize SEC revenue, gross profit, cash flow, balance-sheet, and share facts.
- Estimate market capitalization, dilution, free cash flow, current ratio, and cash runway.
- Preserve calculation versions, source dates, and explicit data-quality flags.
- Schedule derived-field calculation after market and SEC ingestion.
- Add neutral feature-query endpoints and two feature tools to the existing MCP app.

## 0.2.3

- Select the latest eligible weekday strictly before today for default Massive daily-price syncs.
- Resume scheduled price ingestion from the latest stored trade date after failures or downtime.
- Make `sync-market` without `--date` use the same safe incremental catch-up behavior.
- Schedule default market updates Tuesday-Saturday morning so Friday data is collected Saturday.

## 0.2.2

- Build the Python application image once through the `migrate` service.
- Reuse the same local image for the API, worker, and MCP roles without registry pulls.
- Use a stable local image tag to prevent old version-tagged images from accumulating.

## 0.2.1

- Add a pinned OpenAI Secure MCP Tunnel client as a Compose service.
- Route the tunnel to the MCP server over the private Compose network.
- Keep the tunnel ID and runtime API key in `.env`, outside Git.
- Bind the tunnel health/admin UI to Unraid loopback by default.

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
