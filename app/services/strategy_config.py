import copy
import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRATEGY_CONFIG = (
    PROJECT_ROOT / "config" / "strategies" / "fallen-growth-swing-v1.1.0.json"
)
DEFAULT_SIMULATION_CONFIG = PROJECT_ROOT / "config" / "simulations" / "default.json"


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


def load_strategy_configuration(
    path: str | Path | None = None,
) -> dict[str, Any]:
    configuration = _load_json(path or DEFAULT_STRATEGY_CONFIG, "strategy configuration")
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
    return configuration


def load_simulation_configuration(
    path: str | Path | None = None,
) -> dict[str, Any]:
    configuration = _load_json(
        path or DEFAULT_SIMULATION_CONFIG, "simulation configuration"
    )
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


def with_overrides(
    configuration: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    result = copy.deepcopy(configuration)
    for key, value in overrides.items():
        if value is not None:
            result[key] = value
    return result
