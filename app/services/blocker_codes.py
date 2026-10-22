"""Typed research blocker vocabulary for the v0.9 alert.

Research can only veto a server-derived label, and only with one of these
codes. Free-text blockers saved by earlier research are mapped onto the same
codes; technical observations (relative strength, chart, market regime) are not
fundamental blockers because the server re-evaluates them every session.
"""

from __future__ import annotations

import re
from typing import Iterable

BLOCKER_CODES = (
    "going_concern",
    "dilution_material",
    "dilution_overhang_atm_convert",
    "cash_burn_runway",
    "customer_concentration",
    "debt_refinancing",
    "revenue_quality_acquired",
    "event_risk_near_term",
    "accounting_or_legal",
    "other_fundamental",
)

# Not fundamental: re-evaluated deterministically each session, or a data gap.
NON_BLOCKING = {
    "negative_relative_strength",
    "market_regime_block",
    "weak_chart_structure",
    "dropped_from_raw_pool",
    "free_cash_flow_unavailable",
}

# Ordered: the first matching rule wins.
_RULES: tuple[tuple[str, str], ...] = (
    ("going_concern", r"going[ _-]?concern"),
    ("dilution_overhang_atm_convert", r"\batm\b|at[ _-]the[ _-]market|convertible|warrant|shelf"),
    ("dilution_material", r"dilution|share[ _-]count|offering"),
    ("cash_burn_runway", r"cash[ _-]?burn|runway|free[ _-]cash[ _-]flow|operating[ _-]losses|ebitda[ _-]losses|cash[ _-]generation"),
    ("customer_concentration", r"concentration"),
    ("debt_refinancing", r"debt|refinanc|leverage|financing[ _-]dependence"),
    ("revenue_quality_acquired", r"acqui|integration"),
    ("event_risk_near_term", r"earnings[ _-]event|event[ _-]risk|milestone"),
    ("accounting_or_legal", r"accounting|legal|restatement|investigation|auditor"),
)


def blocker_code(text: str) -> str | None:
    """Map one saved blocker (a code or legacy free text) to a code, or None if not blocking."""
    normalized = str(text or "").strip().lower()
    if not normalized or normalized in NON_BLOCKING:
        return None
    if normalized in BLOCKER_CODES:
        return normalized
    for code, pattern in _RULES:
        if re.search(pattern, normalized):
            return code
    return "other_fundamental"


def blocker_codes(texts: Iterable[str]) -> list[str]:
    return sorted({code for code in (blocker_code(text) for text in texts) if code})
