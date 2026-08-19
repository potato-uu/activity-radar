from __future__ import annotations

from calendar import monthrange
from datetime import date
from pathlib import Path
from typing import Any

from ..io import load_structured
from .base import AdapterWindow, RawCandidate


def _text(value: Any) -> str:
    return value.isoformat() if isinstance(value, date) else str(value or "").strip()


def _months(value: Any) -> list[int]:
    values = value if isinstance(value, list) else [value]
    result: list[int] = []
    for item in values:
        try:
            month = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12:
            result.append(month)
    return result


class CalendarSeedAdapter:
    def fetch(self, config: dict[str, Any], window: AdapterWindow) -> list[RawCandidate]:
        params = config.get("params") or {}
        raw_path = Path(str(params.get("path") or "config/annual_calendar.yaml"))
        root = Path(str(config.get("_root") or "."))
        path = raw_path if raw_path.is_absolute() else root / raw_path
        data = load_structured(path) or {}
        entries = data.get("events", data) if isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise RuntimeError("annual_calendar.yaml must contain an events list")

        from ..research import now_iso

        fetched_at = now_iso()
        rows: list[RawCandidate] = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("in_scope", True) is False:
                continue
            city = _text(entry.get("city"))
            if city in {"海外", "Overseas"}:
                continue
            confirmed = entry.get("confirmed_dates") or {}
            confirmed_start = _text(confirmed.get("start")) if isinstance(confirmed, dict) else ""
            confirmed_end = _text(confirmed.get("end")) if isinstance(confirmed, dict) else ""
            candidates: list[tuple[str, str, str, str]] = []
            if confirmed_start:
                candidates.append((confirmed_start, confirmed_end or confirmed_start, "active", "day"))
            else:
                for year in range(window.start.year, window.end.year + 1):
                    for month in _months(entry.get("typical_month")):
                        month_start = date(year, month, 1)
                        month_end = date(year, month, monthrange(year, month)[1])
                        if month_end >= window.start and month_start <= window.end:
                            candidates.append((month_start.isoformat(), month_start.isoformat(), "expected", "month"))
            for start, end, status, precision in candidates:
                start_day = date.fromisoformat(start[:10])
                if not window.start <= start_day <= window.end:
                    continue
                rows.append(
                    RawCandidate(
                        source_id=str(config.get("id") or "calendar-seed"),
                        raw_title=_text(entry.get("name")),
                        raw_date_text=_text(entry.get("last_known_dates")) or start,
                        date_start=start[:10],
                        date_end=end[:10],
                        city=city,
                        venue=city,
                        organizer="",
                        url=_text(entry.get("official_url")),
                        raw_excerpt=_text(entry.get("why_it_matters"))[:600],
                        event_type=_text(entry.get("event_type")) or "峰会",
                        fetched_at=fetched_at,
                        status=status,
                        date_precision=precision,
                        metadata={"calendar_id": _text(entry.get("id")), "last_known_dates": _text(entry.get("last_known_dates"))},
                    )
                )
        rows.sort(key=lambda row: (row.date_start, row.raw_title))
        return rows
