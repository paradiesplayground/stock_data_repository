-- Stock Data Repository v0.4.4 verification queries

SELECT version_num AS alembic_revision
FROM alembic_version;

SELECT
    to_regclass('strategy_tracking.strategy_simulation_runs') AS simulation_runs,
    to_regclass('strategy_tracking.strategy_simulation_trades') AS simulation_trades,
    to_regclass('strategy_tracking.strategy_simulation_equity') AS simulation_equity;

SELECT
    definition.strategy_key,
    definition.version AS strategy_version,
    definition.configuration_fingerprint,
    COUNT(run.run_id) AS replay_runs
FROM strategy_tracking.strategy_definitions AS definition
LEFT JOIN strategy_tracking.strategy_runs AS run
  ON run.strategy_definition_id = definition.id
GROUP BY
    definition.strategy_key,
    definition.version,
    definition.configuration_fingerprint
ORDER BY definition.strategy_key, definition.version;

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
