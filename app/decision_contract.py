import json
from decimal import Decimal
from pathlib import Path
from typing import Any


CONTRACTS_DIR = (
    Path(__file__).resolve().parent.parent
    / "contracts"
)
CONTRACT_PATH = CONTRACTS_DIR / "stock-alert-decision-v0.8.schema.json"
LEGACY_CONTRACT_PATH = CONTRACTS_DIR / "stock-alert-decision-v0.7.schema.json"


def _load_contract(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


DECISION_CONTRACT = _load_contract(CONTRACT_PATH)
LEGACY_DECISION_CONTRACT = _load_contract(LEGACY_CONTRACT_PATH)
DECISION_CONTRACT_VERSION = str(DECISION_CONTRACT["contract_version"])
SUPPORTED_DECISION_CONTRACT_VERSIONS = {
    str(LEGACY_DECISION_CONTRACT["contract_version"]),
    DECISION_CONTRACT_VERSION,
}
DECISION_STATUS_GUIDE = tuple(DECISION_CONTRACT["x-decision-statuses"])
DECISION_STATUSES = {item["status"] for item in DECISION_STATUS_GUIDE}
DECISION_PRIORITY = {
    item["status"]: int(item["priority"]) for item in DECISION_STATUS_GUIDE
}
DECISION_STATUS_DEFINITIONS = {
    item["status"]: item["meaning"] for item in DECISION_STATUS_GUIDE
}
SCREEN_BUCKETS = set(
    DECISION_CONTRACT["properties"]["screen_bucket"]["enum"]
)
TECHNICAL_STATES = set(
    DECISION_CONTRACT["properties"]["technical_state"]["enum"]
)
SEMANTIC_RULES = DECISION_CONTRACT["x-semantic-rules"]
BUY_SETUP_MIN_T1_R = Decimal(SEMANTIC_RULES["buy_setup_min_t1_r"])
BUY_SETUP_MIN_T2_R = Decimal(SEMANTIC_RULES["buy_setup_min_t2_r"])
BUY_SETUP_MAX_PCT_ABOVE_TRIGGER = Decimal(
    SEMANTIC_RULES["buy_now_max_distance_pct"]
)
ALMOST_READY_MAX_DISTANCE_PCT = Decimal(
    SEMANTIC_RULES["almost_ready_max_distance_pct"]
)
CALCULATION_TOLERANCE = Decimal(SEMANTIC_RULES["calculation_tolerance"])

LEGACY_DECISION_STATUS_GUIDE = tuple(
    LEGACY_DECISION_CONTRACT["x-decision-statuses"]
)
LEGACY_DECISION_STATUSES = {
    item["status"] for item in LEGACY_DECISION_STATUS_GUIDE
}
LEGACY_DECISION_PRIORITY = {
    item["status"]: int(item["priority"])
    for item in LEGACY_DECISION_STATUS_GUIDE
}
