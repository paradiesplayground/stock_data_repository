import copy
import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STRATEGY_CONFIG_ROOT = PROJECT_ROOT / "config" / "strategies"
DEFAULT_STRATEGY_CONFIG = (
    STRATEGY_CONFIG_ROOT / "fallen-growth-swing-v1.1.1.json"
)
DEFAULT_SIMULATION_CONFIG = PROJECT_ROOT / "config" / "simulations" / "default.json"
DEFAULT_MARKET_REGIME = {
    "enabled": False,
    "benchmark_ticker": "QQQ",
    "moving_average_sessions": 50,
    "require_close_above_moving_average": True,
    "require_moving_average_rising": False,
}


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    resolved = Path(path)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"{label} file was not found: {resolved}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON: {resolved}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return payload


def configuration_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def list_strategy_profiles() -> list[dict[str, Any]]:
    profiles = []
    for path in sorted(STRATEGY_CONFIG_ROOT.glob("*.json")):
        configuration = load_strategy_configuration(path)
        profiles.append(
            {
                "profile": path.name,
                "strategy_key": configuration["strategy"]["key"],
                "strategy_version": configuration["strategy"]["version"],
                "name": configuration["strategy"]["name"],
                "configuration_fingerprint": configuration_hash(configuration),
            }
        )
    return profiles


def load_strategy_profile(profile: str) -> dict[str, Any]:
    name = profile if profile.endswith(".json") else f"{profile}.json"
    if Path(name).name != name:
        raise ValueError("strategy profile must be a bundled profile name")
    path = STRATEGY_CONFIG_ROOT / name
    return load_strategy_configuration(path)


def load_strategy_configuration(
    path: str | Path | None = None,
) -> dict[str, Any]:
    configuration = _load_json(path or DEFAULT_STRATEGY_CONFIG, "strategy configuration")
    return validate_strategy_configuration(configuration)


def validate_strategy_configuration(
    configuration: dict[str, Any],
) -> dict[str, Any]:
    configuration = copy.deepcopy(configuration)
    required_sections = {
        "strategy",
        "universe",
        "hard_thresholds",
        "scoring",
        "entry_model",
        "risk_tiers",
    }
    missing = sorted(required_sections - configuration.keys())
    if missing:
        raise ValueError(f"strategy configuration is missing: {', '.join(missing)}")
    metadata = configuration["strategy"]
    for field in ("key", "version", "name", "replay_model", "feature_calculation_version"):
        if not metadata.get(field):
            raise ValueError(f"strategy.{field} is required")
    if not configuration["risk_tiers"]:
        raise ValueError("risk_tiers must contain at least one tier")
    market_regime = configuration.get("market_regime")
    if market_regime is not None:
        missing_regime = sorted(DEFAULT_MARKET_REGIME.keys() - market_regime.keys())
        if missing_regime:
            raise ValueError(
                "market_regime is missing: " + ", ".join(missing_regime)
            )
        if not str(market_regime["benchmark_ticker"]).strip():
            raise ValueError("market_regime.benchmark_ticker is required")
        if int(market_regime["moving_average_sessions"]) < 2:
            raise ValueError(
                "market_regime.moving_average_sessions must be at least 2"
            )
        for key in (
            "enabled",
            "require_close_above_moving_average",
            "require_moving_average_rising",
        ):
            if not isinstance(market_regime[key], bool):
                raise ValueError(f"market_regime.{key} must be boolean")
        if market_regime["enabled"] and not (
            market_regime["require_close_above_moving_average"]
            or market_regime["require_moving_average_rising"]
        ):
            raise ValueError(
                "enabled market_regime must require at least one condition"
            )
    return configuration


def load_simulation_configuration(
    path: str | Path | None = None,
) -> dict[str, Any]:
    configuration = _load_json(
        path or DEFAULT_SIMULATION_CONFIG, "simulation configuration"
    )
    return validate_simulation_configuration(configuration)


def validate_simulation_configuration(
    configuration: dict[str, Any],
) -> dict[str, Any]:
    configuration = copy.deepcopy(configuration)
    required = {
        "starting_capital",
        "risk_per_trade_pct",
        "max_total_risk_pct",
        "max_open_positions",
        "slippage_pct",
        "order_lifetime_sessions",
        "max_holding_sessions",
        "execution_rules",
    }
    missing = sorted(required - configuration.keys())
    if missing:
        raise ValueError(f"simulation configuration is missing: {', '.join(missing)}")
    return configuration


def with_nested_overrides(
    configuration: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Apply only known configuration keys, rejecting typo-created settings."""
    result = copy.deepcopy(configuration)

    def merge(target: dict[str, Any], changes: dict[str, Any], prefix: str) -> None:
        for key, value in changes.items():
            path = f"{prefix}.{key}" if prefix else key
            if key not in target:
                raise ValueError(f"unknown configuration setting: {path}")
            if isinstance(value, dict):
                if not isinstance(target[key], dict):
                    raise ValueError(f"configuration setting is not an object: {path}")
                merge(target[key], value, path)
            else:
                target[key] = value

    merge(result, overrides, "")
    return result


def with_overrides(
    configuration: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    result = copy.deepcopy(configuration)
    for key, value in overrides.items():
        if value is not None:
            result[key] = value
    return result
