import copy
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services.strategy_config import (
    DEFAULT_MARKET_REGIME,
    OPTIONAL_MARKET_REGIME,
    configuration_hash,
    load_simulation_configuration,
    load_strategy_profile,
    validate_simulation_configuration,
    validate_strategy_configuration,
    with_nested_overrides,
)
from app.services.strategy_replay import replay_configuration, replay_strategy_range
from app.services.strategy_simulation import SimulationParameters, run_simulation


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
    }
