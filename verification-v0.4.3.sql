-- Stock Data Repository v0.4.3 verification queries

SELECT version_num AS alembic_revision
FROM alembic_version;

SELECT
    to_regclass('strategy_tracking.strategy_simulation_runs') AS simulation_runs,
    to_regclass('strategy_tracking.strategy_simulation_trades') AS simulation_trades,
    to_regclass('strategy_tracking.strategy_simulation_equity') AS simulation_equity;

SELECT
    simulation_id,
    start_date,
    end_date,
    parameters ->> 'scenario_name' AS scenario_name,
    parameters ->> 'starting_capital' AS starting_capital,
    parameters ->> 'risk_per_trade_pct' AS risk_per_trade_pct,
    summary ->> 'total_return_pct' AS total_return_pct,
    summary ->> 'maximum_drawdown_pct' AS maximum_drawdown_pct,
    summary ->> 'closed_trades' AS closed_trades,
    generated_at_utc
FROM strategy_tracking.strategy_simulation_runs
ORDER BY generated_at_utc DESC;

SELECT
    run.simulation_id,
    COUNT(DISTINCT trade.id) AS trade_rows,
    COUNT(DISTINCT equity.id) AS equity_sessions
FROM strategy_tracking.strategy_simulation_runs AS run
LEFT JOIN strategy_tracking.strategy_simulation_trades AS trade
  ON trade.simulation_id = run.simulation_id
LEFT JOIN strategy_tracking.strategy_simulation_equity AS equity
  ON equity.simulation_id = run.simulation_id
GROUP BY run.simulation_id
ORDER BY run.simulation_id;

SELECT
    equity.simulation_id,
    MIN(equity.market_date) AS first_date,
    MAX(equity.market_date) AS last_date,
    MIN(equity.drawdown_pct) AS maximum_drawdown_pct
FROM strategy_tracking.strategy_simulation_equity AS equity
GROUP BY equity.simulation_id;
