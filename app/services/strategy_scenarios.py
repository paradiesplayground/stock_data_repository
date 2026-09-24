import copy
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.services.strategy_config import (
    DEFAULT_MARKET_REGIME,
    OPTIONAL_ACTIONABLE_RULES,
    OPTIONAL_MARKET_REGIME,
    configuration_hash,
    load_simulation_configuration,
    load_strategy_profile,
    validate_simulation_configuration,
    validate_strategy_configuration,
    with_nested_overrides,
)
from app.services.strategy_replay import replay_configuration, replay_strategy_range
from app.services.strategy_decline_analysis import rejected_opportunity_analysis
from app.services.strategy_simulation import SimulationParameters, run_simulation


DECLINE_COMPARISON_SCENARIOS = (
    ("price_change_le_neg20", {"maximum_price_change_12w_pct": "-20", "maximum_drawdown_12w_high_pct": "-20", "decline_filter_mode": "price_change"}),
    ("price_change_le_neg15", {"maximum_price_change_12w_pct": "-15", "maximum_drawdown_12w_high_pct": "-20", "decline_filter_mode": "price_change"}),
    ("price_change_le_neg10", {"maximum_price_change_12w_pct": "-10", "maximum_drawdown_12w_high_pct": "-20", "decline_filter_mode": "price_change"}),
    ("drawdown_le_neg20", {"maximum_price_change_12w_pct": "-20", "maximum_drawdown_12w_high_pct": "-20", "decline_filter_mode": "drawdown_from_high"}),
    ("price_or_drawdown_neg20", {"maximum_price_change_12w_pct": "-20", "maximum_drawdown_12w_high_pct": "-20", "decline_filter_mode": "either"}),
    ("price_neg15_or_drawdown_neg20", {"maximum_price_change_12w_pct": "-15", "maximum_drawdown_12w_high_pct": "-20", "decline_filter_mode": "either"}),
)


def resolve_strategy_scenario(
    base_profile: str,
    strategy_version: str,
    strategy_overrides: dict[str, Any] | None = None,
    simulation_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not strategy_version.strip():
        raise ValueError("strategy_version is required")
    strategy = load_strategy_profile(base_profile)
    strategy_overrides = strategy_overrides or {}
    # Market-regime policy was introduced after the historical bundled profiles.
    # Materialize its known defaults only when a scenario requests the section so
    # the immutable fingerprints of those profiles remain unchanged.
    if "market_regime" in strategy_overrides and "market_regime" not in strategy:
        strategy["market_regime"] = dict(DEFAULT_MARKET_REGIME)
    regime_overrides = strategy_overrides.get("market_regime", {})
    if "market_regime" in strategy:
        for key, default in OPTIONAL_MARKET_REGIME.items():
            if key in regime_overrides and key not in strategy["market_regime"]:
                strategy["market_regime"][key] = copy.deepcopy(default)
    actionable_overrides = strategy_overrides.get("scoring", {}).get(
        "actionable", {}
    )
    actionable = strategy["scoring"]["actionable"]
    for key, default in OPTIONAL_ACTIONABLE_RULES.items():
        if key in actionable_overrides and key not in actionable:
            actionable[key] = copy.deepcopy(default)
    strategy = with_nested_overrides(strategy, strategy_overrides)
    strategy["strategy"]["version"] = strategy_version.strip()
    strategy = validate_strategy_configuration(strategy)
    resolved_strategy = replay_configuration(configuration=strategy)

    simulation = with_nested_overrides(
        load_simulation_configuration(), simulation_overrides or {}
    )
    simulation = validate_simulation_configuration(simulation)
    return {
        "base_profile": base_profile,
        "strategy_configuration": resolved_strategy,
        "strategy_configuration_fingerprint": resolved_strategy[
            "configuration_fingerprint"
        ],
        "simulation_configuration": simulation,
        "simulation_configuration_fingerprint": configuration_hash(simulation),
    }


def run_strategy_scenario(
    session: Session,
    start_date: date,
    end_date: date,
    base_profile: str,
    strategy_version: str,
    strategy_overrides: dict[str, Any] | None = None,
    simulation_overrides: dict[str, Any] | None = None,
    *,
    resume: bool = True,
) -> dict[str, Any]:
    resolved = resolve_strategy_scenario(
        base_profile,
        strategy_version,
        strategy_overrides,
        simulation_overrides,
    )
    strategy = resolved["strategy_configuration"]
    # replay_strategy_range resolves the payload and adds its fingerprint itself.
    replay_payload = {
        key: value for key, value in strategy.items() if key != "configuration_fingerprint"
    }
    replay = replay_strategy_range(
        session,
        start_date,
        end_date,
        resume=resume,
        configuration=replay_payload,
    )
    parameters = SimulationParameters.from_payload(
        resolved["simulation_configuration"]
    )
    simulation = run_simulation(
        session,
        start_date,
        end_date,
        parameters,
        strategy_configuration=replay_payload,
    )
    compact_replay = {
        key: value
        for key, value in replay.items()
        if key not in {"completed_dates", "skipped_dates"}
    }
    return {
        "configuration": resolved,
        "replay": compact_replay,
        "simulation": simulation,
        "rejected_opportunity_analysis": rejected_opportunity_analysis(
            session,
            start_date,
            end_date,
            configuration=strategy,
        ),
    }


def run_decline_filter_comparison(
    session: Session,
    start_date: date,
    end_date: date,
    *,
    base_profile: str = "fallen-growth-swing-v1.2.0.json",
    strategy_version_prefix: str = "1.2.0-decline-study",
    simulation_overrides: dict[str, Any] | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    """Run the fixed decline-screen experiment without altering production rules."""
    results = []
    feature_version = load_strategy_profile(base_profile)["strategy"][
        "feature_calculation_version"
    ]
    calendar_months = max(
        1, (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1
    )
    for name, thresholds in DECLINE_COMPARISON_SCENARIOS:
        version = f"{strategy_version_prefix}-{name}"
        overrides = {"hard_thresholds": thresholds}
        try:
            result = run_strategy_scenario(
                session,
                start_date,
                end_date,
                base_profile,
                version,
                overrides,
                simulation_overrides,
                resume=resume,
            )
            simulation_status = "completed"
        except RuntimeError as error:
            if not str(error).startswith("No actionable deterministic replay signals"):
                raise
            resolved = resolve_strategy_scenario(
                base_profile, version, overrides, simulation_overrides
            )
            strategy = resolved["strategy_configuration"]
            replay = replay_strategy_range(
                session,
                start_date,
                end_date,
                resume=True,
                configuration={
                    key: value
                    for key, value in strategy.items()
                    if key != "configuration_fingerprint"
                },
            )
            result = {
                "replay": replay,
                "simulation": {"summary": {
                    "signals": 0, "filled_trades": 0, "closed_trades": 0,
                    "expectancy_r": None, "win_rate_pct": None,
                    "profit_factor": None, "total_return_pct": "0",
                    "maximum_drawdown_pct": "0",
                }},
                "rejected_opportunity_analysis": rejected_opportunity_analysis(
                    session, start_date, end_date, configuration=strategy
                ),
            }
            simulation_status = "no_actionable_signals"
        simulation = result["simulation"]["summary"]
        results.append({
            "scenario": name,
            "decline_filter": thresholds,
            "simulation_status": simulation_status,
            "candidate_days": result["replay"]["raw_candidate_count"],
            "signals": simulation["signals"],
            "fills": simulation["filled_trades"],
            "closed_trades": simulation["closed_trades"],
            "trades_per_month": str(
                Decimal(str(simulation["closed_trades"])) / Decimal(calendar_months)
            ),
            "expectancy_r": simulation["expectancy_r"],
            "win_rate_pct": simulation["win_rate_pct"],
            "profit_factor": simulation["profit_factor"],
            "total_return_pct": simulation["total_return_pct"],
            "maximum_drawdown_pct": simulation["maximum_drawdown_pct"],
            "rejected_opportunity_forward_outcomes": result[
                "rejected_opportunity_analysis"
            ]["forward_outcomes"],
        })
    return {
        "report_type": "decline_filter_comparison",
        "base_profile": base_profile,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "feature_calculation_version": feature_version,
        "constant_parameters": {
            "simulation_overrides": simulation_overrides or {},
            "all_non_decline_strategy_parameters": "base profile unchanged",
        },
        "scenarios": results,
    }
