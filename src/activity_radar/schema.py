from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

EVENT_FIELDS = (
    "id", "name", "name_en", "date_start", "date_end", "city", "venue",
    "organizer", "url", "ticket_price", "register_deadline", "event_type",
    "acquisition_score", "ecosystem_score", "tier", "action", "reason",
    "source", "first_seen", "last_verified", "status", "date_precision",
    "audience_side", "scale_hint", "format", "is_series", "occurrences",
    "side_event_opportunity", "related_to", "score_history", "needs_review",
)


@dataclass
class Event:
    id: str
    name: str
    name_en: str = ""
    date_start: str = ""
    date_end: str = ""
    city: str = "上海"
    venue: str = ""
    organizer: str = ""
    url: str = ""
    ticket_price: str = ""
    register_deadline: str = ""
    event_type: str = "峰会"
    acquisition_score: float = 0.0
    ecosystem_score: float = 0.0
    tier: str = "C"
    action: str = "watch_content"
    reason: str = ""
    source: str = "manual"
    first_seen: str = ""
    last_verified: str = ""
    status: str = "active"
    date_precision: str = "day"
    audience_side: str = "mixed"
    scale_hint: str = "unknown"
    format: str = "open"
    is_series: bool = False
    occurrences: list[str] = field(default_factory=list)
    side_event_opportunity: bool = False
    related_to: str = ""
    score_history: list[dict[str, Any]] = field(default_factory=list)
    needs_review: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        values = {key: data.get(key, getattr(cls, key, "")) for key in EVENT_FIELDS}
        values["acquisition_score"] = float(values.get("acquisition_score") or 0)
        values["ecosystem_score"] = float(values.get("ecosystem_score") or 0)
        values["is_series"] = bool(values.get("is_series"))
        values["occurrences"] = list(values.get("occurrences") or [])
        values["side_event_opportunity"] = bool(values.get("side_event_opportunity"))
        values["score_history"] = list(values.get("score_history") or [])
        values["needs_review"] = bool(values.get("needs_review"))
        values["metadata"] = data.get("metadata", {})
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result = {field: getattr(self, field) for field in EVENT_FIELDS}
        if self.metadata:
            result["metadata"] = self.metadata
        return result


def validate_event(event: Event) -> list[str]:
    errors: list[str] = []
    if not event.id or not event.name:
        errors.append("id and name are required")
    if not event.url:
        errors.append("url is required")
    if not event.city:
        errors.append("city is required")
    if not 0 <= event.acquisition_score <= 10:
        errors.append("acquisition_score must be 0..10")
    if not 0 <= event.ecosystem_score <= 10:
        errors.append("ecosystem_score must be 0..10")
    if event.tier not in {"A", "B", "C", "D"}:
        errors.append("tier must be A, B, C, or D")
    if event.status not in {"active", "expected", "changed", "cancelled"}:
        errors.append("status must be active, expected, changed, or cancelled")
    return errors


def parse_iso(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
