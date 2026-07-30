-- Stock Data Repository v0.4.16 verification

SELECT version_num
FROM alembic_version;

SELECT
    'adjusted' AS price_basis,
    MIN(trade_date) AS earliest_date,
    MAX(trade_date) AS latest_date,
    COUNT(*) AS rows,
    COUNT(DISTINCT ticker) AS tickers
FROM daily_price_bars
UNION ALL
SELECT
    'unadjusted',
    MIN(trade_date),
    MAX(trade_date),
    COUNT(*),
    COUNT(DISTINCT ticker)
FROM raw_daily_price_bars;

SELECT
    adjusted.trade_date,
    adjusted.rows AS adjusted_rows,
    COALESCE(raw.rows, 0) AS unadjusted_rows,
    adjusted.rows - COALESCE(raw.rows, 0) AS difference
FROM (
    SELECT trade_date, COUNT(*) AS rows
    FROM daily_price_bars
    GROUP BY trade_date
) AS adjusted
LEFT JOIN (
    SELECT trade_date, COUNT(*) AS rows
    FROM raw_daily_price_bars
    GROUP BY trade_date
) AS raw USING (trade_date)
WHERE adjusted.rows <> COALESCE(raw.rows, 0)
ORDER BY adjusted.trade_date DESC
LIMIT 25;

SELECT
    'splits' AS dataset,
    MIN(execution_date) AS earliest_date,
    MAX(execution_date) AS latest_date,
    COUNT(*) AS rows
FROM stock_splits
UNION ALL
SELECT
    'dividends',
    MIN(ex_dividend_date),
    MAX(ex_dividend_date),
    COUNT(*)
FROM cash_dividends
UNION ALL
SELECT
    'ticker_events',
    MIN(event_date),
    MAX(event_date),
    COUNT(*)
FROM ticker_events;

SELECT
    MIN(as_of_date) AS earliest_reference_date,
    MAX(as_of_date) AS latest_reference_date,
    COUNT(*) AS changed_reference_states,
    COUNT(DISTINCT ticker) AS tickers
FROM security_reference_snapshots;

SELECT
    ticker,
    as_of_date,
    snapshot ->> 'name' AS name,
    snapshot ->> 'primary_exchange' AS primary_exchange,
    snapshot ->> 'active' AS active,
    snapshot ->> 'composite_figi' AS composite_figi
FROM security_reference_snapshots
WHERE ticker = 'TWTR'
ORDER BY as_of_date;

SELECT
    ticker,
    event_date,
    event_type,
    identifier,
    details
FROM ticker_events
WHERE ticker IN ('FB', 'META', 'TWTR')
ORDER BY identifier, event_date;
