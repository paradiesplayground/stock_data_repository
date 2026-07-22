-- Stock Data Repository v0.4.2 verification queries

SELECT version_num AS alembic_revision
FROM alembic_version;

SELECT
    to_regclass('strategy_tracking.strategy_simulation_runs') AS simulation_runs,
    to_regclass('strategy_tracking.strategy_simulation_trades') AS simulation_trades,
    to_regclass('strategy_tracking.strategy_simulation_equity') AS simulation_equity;

SELECT
    definition.strategy_key,
    definition.version AS strategy_version,
    COUNT(*) AS replay_sessions,
    MIN(run.as_of_date) AS first_date,
    MAX(run.as_of_date) AS last_date,
    SUM((run.summary ->> 'actionable_count')::integer) AS actionable_signals
FROM strategy_tracking.strategy_runs AS run
JOIN strategy_tracking.strategy_definitions AS definition
  ON definition.id = run.strategy_definition_id
WHERE run.run_type = 'backtest'
  AND definition.strategy_key = 'fallen-growth-swing'
  AND definition.version = '1.1.0'
GROUP BY definition.strategy_key, definition.version;

SELECT
    simulation_id,
    start_date,
    end_date,
    parameters ->> 'starting_capital' AS starting_capital,
    parameters ->> 'risk_per_trade_pct' AS risk_per_trade_pct,
    summary ->> 'total_return_pct' AS total_return_pct,
    summary ->> 'maximum_drawdown_pct' AS maximum_drawdown_pct,
    summary ->> 'closed_trades' AS closed_trades,
    summary ->> 'expectancy_r' AS expectancy_r,
    generated_at_utc
FROM strategy_tracking.strategy_simulation_runs
ORDER BY generated_at_utc DESC;

SELECT
    simulation_id,
    status,
    COUNT(*) AS signals,
    SUM(net_pnl) AS realized_pnl
FROM strategy_tracking.strategy_simulation_trades
GROUP BY simulation_id, status
ORDER BY simulation_id, status;

SELECT
    simulation_id,
    COUNT(*) AS equity_sessions,
    MIN(market_date) AS first_date,
    MAX(market_date) AS last_date,
    MIN(drawdown_pct) AS maximum_drawdown_pct
FROM strategy_tracking.strategy_simulation_equity
GROUP BY simulation_id;
