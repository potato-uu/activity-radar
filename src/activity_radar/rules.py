from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any, Iterable

from .schema import Event, parse_iso, validate_event


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", name.lower())


def make_id(name: str, date_start: str, url: str = "") -> str:
    raw = f"{normalize_name(name)}|{date_start[:10]}|{url}".encode("utf-8")
    return "evt-" + hashlib.sha1(raw).hexdigest()[:12]


def same_event(left: Event, right: Event) -> bool:
    score = SequenceMatcher(None, normalize_name(left.name), normalize_name(right.name)).ratio()
    if left.url and right.url and left.url.rstrip("/") == right.url.rstrip("/") and score >= 0.88:
        return True
    ldate, rdate = parse_iso(left.date_start), parse_iso(right.date_start)
    if ldate and rdate and abs((ldate - rdate).days) > 3:
        return False
    if left.url and right.url and left.url.rstrip("/") == right.url.rstrip("/"):
        return score >= 0.88 or (ldate == rdate and score >= 0.65)
    return score >= 0.78


def classify_tier(acquisition: float, ecosystem: float, city: str, event_type: str, scoring: dict[str, Any]) -> str:
    raw = max(acquisition, ecosystem)
    rules = scoring.get("tier_rules", {"A": 8, "B": 6, "C": 4})
    if city not in {"上海", "Shanghai"} or event_type.lower() == "webinar":
        return "C"
    if raw >= float(rules.get("A", 8)):
        return "A"
    if raw >= float(rules.get("B", 6)):
        return "B"
    if raw >= float(rules.get("C", 4)):
        return "C"
    return "D"


def choose_action(event: Event) -> str:
    if event.tier == "A":
        return "attend"
    if event.tier == "B":
        return "send_colleague" if event.ecosystem_score > event.acquisition_score else "attend"
    return "watch_content"


def prepare_event(raw: dict[str, Any], source_id: str, now: str, scoring: dict[str, Any]) -> Event:
    event = Event.from_dict({
        **raw,
        "id": raw.get("id") or make_id(raw.get("name", ""), raw.get("date_start", ""), raw.get("url", "")),
        "source": raw.get("source") or source_id,
        "first_seen": raw.get("first_seen") or now,
        "last_verified": now,
        "status": raw.get("status", "active"),
    })
    event.tier = classify_tier(event.acquisition_score, event.ecosystem_score, event.city, event.event_type, scoring)
    event.action = raw.get("action") or choose_action(event)
    return event


def merge_events(existing: Iterable[Event], candidates: Iterable[Event], city_scope: set[str], scoring: dict[str, Any]) -> tuple[list[Event], dict[str, int]]:
    merged = [event for event in existing if event.city in city_scope and event.tier != "D"]
    stats = {"new": 0, "changed": 0, "unchanged": 0, "dropped": 0, "invalid": 0}
    mutable_fields = ("name", "name_en", "date_start", "date_end", "city", "venue", "organizer", "url", "ticket_price", "register_deadline", "event_type", "acquisition_score", "ecosystem_score", "tier", "action", "reason", "source")
    for candidate in candidates:
        errors = validate_event(candidate)
        if errors or candidate.city not in city_scope or candidate.tier == "D":
            stats["invalid" if errors else "dropped"] += 1
            continue
        match = next((event for event in merged if same_event(event, candidate)), None)
        if match is None:
            merged.append(candidate)
            stats["new"] += 1
            continue
        changed = any(getattr(match, field) != getattr(candidate, field) for field in mutable_fields)
        if changed:
            first_seen = match.first_seen
            for field in mutable_fields:
                setattr(match, field, getattr(candidate, field))
            match.first_seen = first_seen
            match.last_verified = candidate.last_verified
            match.status = "changed"
            stats["changed"] += 1
        else:
            match.last_verified = candidate.last_verified
            stats["unchanged"] += 1
    merged.sort(key=lambda item: (item.date_start or "9999-12-31", item.name))
    return merged, stats


def upcoming(events: Iterable[Event], today: date, days: int = 60) -> list[Event]:
    end = today + timedelta(days=days)
    return [event for event in events if (start := parse_iso(event.date_start)) and today <= start <= end and event.status != "cancelled"]
