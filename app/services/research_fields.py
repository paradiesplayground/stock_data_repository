"""Typed research findings recorded with each deep-research checkpoint.

These are measured before they are trusted: the server stores them with the
candidate and the outcome scorecard can break results down by each field.
They do not change any label until a field has shown it separates outcomes
(the same evidence bar as a strategy change).
"""

from __future__ import annotations

from datetime import date
from typing import Any

FIELD_CHOICES: dict[str, tuple[str, ...]] = {
    "guidance_direction": ("raised", "held", "cut", "none", "unknown"),
    "gross_margin_trend": ("expanding", "stable", "contracting", "unknown"),
    "estimate_revisions": ("up", "flat", "down", "unknown"),
    "insider_activity": ("net_buying", "neutral", "net_selling", "unknown"),
    "institutional_activity": ("accumulating", "neutral", "distributing", "unknown"),
    "catalyst_quality": ("strong", "neutral", "weak", "unknown"),
}
DATE_FIELDS = ("next_earnings_date",)
FIELD_NAMES = (*DATE_FIELDS, *FIELD_CHOICES)


def normalize_research_fields(value: Any) -> dict[str, Any] | None:
    """Validate typed findings; omitted fields are recorded as unknown."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("research_fields must be an object")
    unexpected = sorted(set(value) - set(FIELD_NAMES))
    if unexpected:
        raise ValueError("unknown research_fields: " + ", ".join(unexpected))
    normalized: dict[str, Any] = {}
    for field in DATE_FIELDS:
        raw = value.get(field)
        if raw in (None, "", "unknown"):
            normalized[field] = None
            continue
        try:
            normalized[field] = date.fromisoformat(str(raw)).isoformat()
        except ValueError as error:
            raise ValueError(f"research_fields.{field} must be an ISO date or null") from error
    for field, choices in FIELD_CHOICES.items():
        raw = str(value.get(field) or "unknown").strip().lower()
        if raw not in choices:
            raise ValueError(f"research_fields.{field} must be one of: " + ", ".join(choices))
        normalized[field] = raw
    return normalized


def earnings_bucket(fields: dict[str, Any] | None, as_of_date: date) -> str:
    """Calendar distance from the alert date to the next reported earnings date."""
    raw = (fields or {}).get("next_earnings_date")
    if not raw:
        return "unknown"
    days = (date.fromisoformat(raw) - as_of_date).days
    if days < 0:
        return "unknown"
    if days <= 7:
        return "within_7_days"
    if days <= 30:
        return "8_to_30_days"
    return "over_30_days"


def research_summary(fields: dict[str, Any] | None, as_of_date: date) -> str | None:
    """One readable line for cards; unknown values are left out."""
    if not fields:
        return None
    parts = []
    if fields.get("next_earnings_date"):
        days = (date.fromisoformat(fields["next_earnings_date"]) - as_of_date).days
        if days >= 0:
            parts.append(f"earnings {fields['next_earnings_date']} (in {days} days)")
    labels = {
        "guidance_direction": "guidance {}",
        "gross_margin_trend": "margins {}",
        "estimate_revisions": "estimates revised {}",
        "insider_activity": "insiders {}",
        "institutional_activity": "institutions {}",
        "catalyst_quality": "catalyst {}",
    }
    for field, template in labels.items():
        value = fields.get(field)
        if value and value not in {"unknown", "none"}:
            parts.append(template.format(value.replace("_", " ")))
    return "; ".join(parts) or None
