from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Protocol


@dataclass(frozen=True)
class AdapterWindow:
    start: date
    end: date


@dataclass
class RawCandidate:
    source_id: str
    raw_title: str
    raw_date_text: str
    url: str
    fetched_at: str
    date_start: str = ""
    date_end: str = ""
    city: str = ""
    venue: str = ""
    organizer: str = ""
    raw_excerpt: str = ""
    event_type: str = ""
    status: str = "active"
    date_precision: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceAdapter(Protocol):
    def fetch(self, config: dict[str, Any], window: AdapterWindow) -> list[RawCandidate]: ...
