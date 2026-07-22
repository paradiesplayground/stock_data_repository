import argparse
import json
from datetime import date
from decimal import Decimal

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.services.feature_calculation import (
    backfill_daily_features,
    calculate_daily_features,
)
from app.services.feature_validation import validate_feature_calculations
from app.services.massive_ingestion import (
    backfill_market_data,
    sync_market_incremental,
    sync_market_day,
    sync_reference_data,
)
from app.services.sec_ingestion import (
    sync_companyfacts,
    sync_sec_all,
    sync_sec_incremental,
    sync_submissions,
)
from app.services.strategy_replay import replay_strategy_range
from app.services.strategy_simulation import (
    SimulationParameters,
    get_simulation,
    list_simulations,
    run_simulation,
)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _decimal(value: str) -> Decimal:
    return Decimal(value)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Stock data ingestion jobs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    reference = subparsers.add_parser("sync-reference")
    reference.add_argument("--include-inactive", action="store_true")
    market = subparsers.add_parser("sync-market")
    market.add_argument("--date", type=_date)
    backfill = subparsers.add_parser("backfill-market")
    backfill.add_argument("--start", type=_date)
    backfill.add_argument("--end", type=_date)
    features = subparsers.add_parser("sync-features")
    features.add_argument("--date", type=_date)
    feature_backfill = subparsers.add_parser("backfill-features")
    feature_backfill.add_argument("--start", type=_date, required=True)
    feature_backfill.add_argument("--end", type=_date, required=True)
    feature_backfill.add_argument("--resume", action="store_true")
    replay = subparsers.add_parser("replay-strategy")
    replay.add_argument("--start", type=_date, required=True)
    replay.add_argument("--end", type=_date, required=True)
    replay.add_argument("--resume", action="store_true")
    replay.add_argument("--strategy-config")
    simulation = subparsers.add_parser("simulate-strategy")
    simulation.add_argument("--start", type=_date, required=True)
    simulation.add_argument("--end", type=_date, required=True)
    simulation.add_argument("--strategy-config")
    simulation.add_argument("--simulation-config")
    simulation.add_argument("--starting-capital", type=_decimal)
    simulation.add_argument("--risk-per-trade-pct", type=_decimal)
    simulation.add_argument("--max-total-risk-pct", type=_decimal)
    simulation.add_argument("--max-open-positions", type=int)
    simulation.add_argument("--slippage-pct", type=_decimal)
    simulation.add_argument("--order-lifetime-sessions", type=int)
    simulation.add_argument("--max-holding-sessions", type=int)
    simulations = subparsers.add_parser("list-simulations")
    simulations.add_argument("--limit", type=int, default=20)
    simulation_detail = subparsers.add_parser("get-simulation")
    simulation_detail.add_argument("--simulation-id", required=True)
    validation = subparsers.add_parser("validate-features")
    validation.add_argument("--ticker", action="append", required=True)
    validation.add_argument("--date", type=_date)
    subparsers.add_parser("sync-companyfacts")
    subparsers.add_parser("sync-submissions")
    subparsers.add_parser("sync-sec")
    subparsers.add_parser("sync-sec-incremental")

    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        if args.command == "sync-reference":
            result = sync_reference_data(
                session,
                settings,
                include_inactive=args.include_inactive,
            )
        elif args.command == "sync-market":
            result = (
                sync_market_day(
                    session,
                    settings,
                    args.date,
                    validate_completeness=True,
                )
                if args.date
                else sync_market_incremental(session, settings)
            )
        elif args.command == "backfill-market":
            result = backfill_market_data(session, settings, args.start, args.end)
        elif args.command == "sync-features":
            result = calculate_daily_features(session, settings, args.date)
        elif args.command == "backfill-features":
            result = backfill_daily_features(
                session,
                settings,
                args.start,
                args.end,
                resume=args.resume,
                configuration_path=args.strategy_config,
            )
        elif args.command == "replay-strategy":
            result = replay_strategy_range(
                session,
                args.start,
                args.end,
                resume=args.resume,
            )
        elif args.command == "simulate-strategy":
            result = run_simulation(
                session,
                args.start,
                args.end,
                SimulationParameters.from_configuration(
                    args.simulation_config,
                    starting_capital=args.starting_capital,
                    risk_per_trade_pct=args.risk_per_trade_pct,
                    max_total_risk_pct=args.max_total_risk_pct,
                    max_open_positions=args.max_open_positions,
                    slippage_pct=args.slippage_pct,
                    order_lifetime_sessions=args.order_lifetime_sessions,
                    max_holding_sessions=args.max_holding_sessions,
                ),
                strategy_configuration_path=args.strategy_config,
            )
        elif args.command == "list-simulations":
            result = list_simulations(session, args.limit)
        elif args.command == "get-simulation":
            result = get_simulation(session, args.simulation_id)
        elif args.command == "validate-features":
            result = validate_feature_calculations(session, args.ticker, args.date)
        elif args.command == "sync-companyfacts":
            result = sync_companyfacts(session, settings)
        elif args.command == "sync-submissions":
            result = sync_submissions(session, settings)
        elif args.command == "sync-sec":
            result = sync_sec_all(session, settings)
        else:
            result = sync_sec_incremental(session, settings)
    if args.command in {
        "validate-features",
        "replay-strategy",
        "simulate-strategy",
        "list-simulations",
        "get-simulation",
    }:
        print(json.dumps(result, indent=2))
    else:
        print(result)


if __name__ == "__main__":
    main()
