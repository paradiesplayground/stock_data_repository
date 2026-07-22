# Changelog

## 0.4.7

- Add a configuration-driven market-regime gate for new entries using a benchmark close versus a
  configurable moving average, with an optional rising-average requirement.
- Evaluate entry permission from the prior session close to prevent look-ahead bias while allowing
  existing positions, stops, targets, and time exits to continue normally.
- Preserve historical bundled-profile fingerprints by materializing the optional regime section
  only for scenarios that request it.

## 0.4.6

- Restore the Moderate `1.1.1`, Expanded `1.1.2`, and Discovery `1.1.3` strategy profiles to the
  tracked repository so clean Git deployments expose the same scenario bases as prior releases.
- Preserve the original configuration fingerprints used by historical replay and simulation runs.

## 0.4.5

- Fix `backfill-features` CLI dispatch after strategy configuration support accidentally made the
  command access an argument defined only for replay and simulation commands.
- Add regression coverage proving resumable feature backfills remain strategy-neutral.

## 0.4.4

- Add MCP tools to list and inspect strategy profiles, preview configuration-only overrides,
  and run replay plus simulation in one call from a downstream skill.
- Accept validated in-memory strategy and simulation configurations so scenario tuning does not
  require copying files into a container or changing Python.
- Reject unknown override keys to prevent misspelled settings from being silently ignored.
- Wire the existing CLI `replay-strategy --strategy-config` argument through to the replay engine.
- Keep all scenario writes isolated under `strategy_tracking`; Massive, SEC, and feature records
  remain unchanged.
- Add strategy profile `1.1.1`, declaring daily percent movement as versioned alert-reporting
  configuration while preserving `1.1.0` for rollback.

## 0.4.3

- Fix simulation persistence so the parent run is inserted before its trade
  and equity rows, preventing PostgreSQL foreign-key failures.

## 0.4.2

- Add a point-in-time mechanical replay for `fallen-growth-swing:1.1.0` over feature version
  `1.3.0`, with explicit qualitative-data limitations.
- Add variable-capital and variable-risk portfolio simulations with cash, position-count,
  aggregate-risk, slippage, order-expiration, partial-target, stop, gap, and time-exit rules.
- Persist immutable scenario parameters, every signal/fill/rejection, summary metrics, and the
  daily equity curve under `strategy_tracking`.
- Add `replay-strategy`, `simulate-strategy`, `list-simulations`, and `get-simulation` CLI commands.
- Add migration `0005_strategy_backtesting` without changing source or feature tables.
- Move replay thresholds, scoring bands, risk tiers, trade-plan multiples, and simulation defaults
  into versioned JSON profiles, with configuration fingerprints stored on every replay.
- Add `--strategy-config` and `--simulation-config` so scenario changes never rewrite raw or
  derived market data and do not require Python edits.

## 0.4.1

- Add an opt-in inactive-ticker reference reconciliation for survivorship-safe historical work.
- Build historical feature universes from exact-session price bars instead of today's active flag.
- Resolve reference history as of the requested session, with an explicit imputation flag when
  only later best-known metadata is available.
- Bump derived features to `1.3.0` while preserving prior calculation versions.
- Add a resumable QQQ-session-driven `backfill-features` CLI command.

## 0.4.0

- Preserve distinct Massive/SEC security-reference states and future daily-price revisions.
- Timestamp SEC financial facts by filing acceptance time for point-in-time replays.
- Make derived snapshots coexist by ticker, date, and calculation version and record their source
  cutoff and source manifest.
- Add 20-session return, 12-week-high drawdown, 20/60-session ranges, ATR(14), overnight gap, and
  relative 20-session performance versus QQQ.
- Add an isolated `strategy_tracking` schema for immutable strategy definitions, as-run/replay/
  backtest records, candidates, evidence, and append-only outcome observations.
- Add read tools for historical security states, price revisions, and strategy runs.
- Add opt-in MCP strategy-write tools and authenticated HTTP strategy-write routes while keeping
  Massive, SEC, and derived repository data read-only to clients.
- Default to the existing `stock_data_repo` Compose project name to prevent upgrade-time container
  name conflicts.

## 0.3.3

- Add a versioned readable taxonomy containing all 10 SIC divisions and all 83 SIC major groups.
- Add curated cross-division healthcare and healthcare-subgroup choices.
- Accept readable labels or stable hierarchy keys through `exclude_industry_groups`.
- Echo resolved labels, hierarchy levels, and underlying SIC prefixes in query responses.
- Add readable industry classification metadata to security and feature results.
- Add a read-only industry-hierarchy HTTP endpoint and MCP discovery tool.
- Preserve `exclude_sic_prefixes` and `exclude_healthcare` as compatibility inputs.

## 0.3.2

- Add caller-supplied SEC SIC-prefix exclusions to neutral feature queries while preserving the
  healthcare compatibility flag and retaining securities with unknown SIC codes.
- Keep the stored feature universe industry-inclusive so downstream skills own strategy policy.
- Include current marketable securities in cash-and-short-term-investment calculations.
- Include standalone commercial paper and short-term borrowing aggregates in total debt without
  double counting reported aggregates.
- Select the freshest instant fact across equivalent SEC concepts instead of allowing a stale
  preferred alias to win.
- Bump the derived calculation version to `1.1.0` and add a read-only feature validation command.

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
