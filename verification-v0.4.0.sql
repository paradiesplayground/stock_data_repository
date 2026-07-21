-- Stock Data Repository v0.4.0 verification queries
-- Run after the stack migration and one `sync-features` job:
-- docker exec -i stock-data-postgres psql -U stockdata -d stockdata \
--   < verification-v0.4.0.sql

\pset pager off
\timing on

-- 1. The migration must report the v0.4.0 revision.
SELECT version_num AS alembic_revision
FROM alembic_version;

-- 2. Confirm the new public history tables and isolated strategy schema exist.
SELECT
    to_regclass('public.security_reference_history') AS reference_history,
    to_regclass('public.daily_price_bar_revisions') AS price_revisions,
    to_regclass('strategy_tracking.strategy_definitions') AS strategy_definitions,
    to_regclass('strategy_tracking.strategy_runs') AS strategy_runs,
    to_regclass('strategy_tracking.strategy_candidates') AS strategy_candidates,
    to_regclass('strategy_tracking.strategy_evidence') AS strategy_evidence,
    to_regclass('strategy_tracking.strategy_outcome_observations') AS strategy_outcomes;

-- 3. Existing securities should have a migration-bootstrap history row.
SELECT
    (SELECT COUNT(*) FROM securities) AS current_securities,
    COUNT(*) AS reference_snapshots,
    COUNT(DISTINCT ticker) AS tickers_with_history,
    COUNT(*) FILTER (WHERE source = 'migration-bootstrap') AS bootstrap_snapshots
FROM security_reference_history;

-- 4. Price revisions accumulate only when a re-fetch actually changes a provider record.
-- Zero is valid immediately after upgrade and remains valid when re-fetched bars are unchanged.
SELECT
    COUNT(*) AS retained_price_revisions,
    COUNT(DISTINCT (ticker, trade_date)) AS revised_ticker_dates,
    MIN(observed_at_utc) AS first_observed,
    MAX(observed_at_utc) AS last_observed
FROM daily_price_bar_revisions;

-- 5. Show all feature versions and coverage. Version 1.2.0 should appear after sync-features.
SELECT
    calculation_version,
    COUNT(*) AS rows,
    COUNT(DISTINCT ticker) AS tickers,
    MIN(as_of_date) AS first_date,
    MAX(as_of_date) AS latest_date,
    COUNT(*) FILTER (WHERE reference_active IS NULL) AS missing_reference_state,
    COUNT(*) FILTER (WHERE source_data_cutoff_utc IS NULL) AS missing_source_cutoff,
    COUNT(*) FILTER (WHERE source_manifest IS NULL) AS missing_source_manifest
FROM security_daily_features
GROUP BY calculation_version
ORDER BY calculation_version;

-- 6. Verify that the new price-derived fields are populated on the latest 1.2.0 snapshot.
WITH latest AS (
    SELECT MAX(as_of_date) AS as_of_date
    FROM security_daily_features
    WHERE calculation_version = '1.2.0'
)
SELECT
    f.as_of_date,
    COUNT(*) AS rows,
    COUNT(f.price_change_20d_pct) AS has_20d_return,
    COUNT(f.drawdown_12w_high_pct) AS has_12w_drawdown,
    COUNT(f.atr_14_pct) AS has_atr,
    COUNT(f.overnight_gap_pct) AS has_gap,
    COUNT(f.relative_return_20d_vs_qqq_pct) AS has_qqq_relative_return
FROM security_daily_features AS f
JOIN latest ON latest.as_of_date = f.as_of_date
WHERE f.calculation_version = '1.2.0'
GROUP BY f.as_of_date;

-- 7. The versioned feature key must have no duplicates.
SELECT ticker, as_of_date, calculation_version, COUNT(*) AS duplicate_count
FROM security_daily_features
GROUP BY ticker, as_of_date, calculation_version
HAVING COUNT(*) > 1;

-- 8. SEC facts linked to an accepted filing should have a point-in-time availability timestamp.
SELECT
    COUNT(*) FILTER (WHERE filing.accepted_at IS NOT NULL) AS linked_to_accepted_filing,
    COUNT(*) FILTER (
        WHERE filing.accepted_at IS NOT NULL
          AND fact.available_at_utc IS NULL
    ) AS unexpectedly_missing_availability,
    COUNT(*) FILTER (WHERE fact.available_at_utc IS NOT NULL) AS facts_with_availability
FROM financial_facts AS fact
LEFT JOIN filings AS filing
  ON filing.accession_number = fact.accession_number;

-- 9. Source values remain internally sane on the latest price date.
WITH latest AS (
    SELECT MAX(trade_date) AS trade_date FROM daily_price_bars
)
SELECT
    latest.trade_date,
    COUNT(*) AS bars,
    COUNT(DISTINCT ticker) AS tickers,
    COUNT(*) FILTER (
        WHERE open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR volume < 0
           OR high < GREATEST(open, close, low)
           OR low > LEAST(open, close, high)
    ) AS invalid_bars
FROM daily_price_bars
JOIN latest USING (trade_date)
GROUP BY latest.trade_date;

-- 10. Strategy counts may be zero before the first alert is recorded.
SELECT
    (SELECT COUNT(*) FROM strategy_tracking.strategy_definitions) AS definitions,
    (SELECT COUNT(*) FROM strategy_tracking.strategy_runs) AS runs,
    (SELECT COUNT(*) FROM strategy_tracking.strategy_candidates) AS candidates,
    (SELECT COUNT(*) FROM strategy_tracking.strategy_evidence) AS evidence,
    (SELECT COUNT(*) FROM strategy_tracking.strategy_outcome_observations) AS outcomes;

-- 11. Recent strategy runs and their candidate totals, after the first recorded alert.
SELECT
    run.run_id,
    definition.strategy_key,
    definition.version AS strategy_version,
    run.as_of_date,
    run.run_type,
    run.feature_calculation_version,
    run.generated_at_utc,
    COUNT(candidate.id) AS candidates
FROM strategy_tracking.strategy_runs AS run
JOIN strategy_tracking.strategy_definitions AS definition
  ON definition.id = run.strategy_definition_id
LEFT JOIN strategy_tracking.strategy_candidates AS candidate
  ON candidate.run_id = run.run_id
GROUP BY run.run_id, definition.strategy_key, definition.version
ORDER BY run.generated_at_utc DESC
LIMIT 20;

-- 12. Database and major relation sizes for capacity tracking.
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;

SELECT
    schemaname,
    relname,
    pg_size_pretty(pg_total_relation_size(format('%I.%I', schemaname, relname))) AS total_size
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(format('%I.%I', schemaname, relname)) DESC
LIMIT 20;
