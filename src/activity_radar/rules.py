from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any, Iterable

from .normalization import infer_city
from .schema import Event, parse_iso, validate_event


def _strip_series_issue(name: str) -> str:
    value = re.sub(r"(?i)\bvol(?:ume)?\.?\s*\d+\b", "", name)
    value = re.sub(r"(?:第\s*)?\d+\s*期|第\s*\d+\s*(?:届|场)", "", value)
    return value


def normalize_name(name: str) -> str:
    value = _strip_series_issue(name)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def significant_name_tokens(name: str) -> set[str]:
    value = re.sub(r"20\d{2}", " ", name.lower())
    generic = {"shanghai", "hangzhou", "suzhou", "nanjing", "ningbo", "wuxi", "hefei", "jiaxing", "nantong", "summit", "conference", "event", "events", "meetup", "forum", "day"}
    latin = {token for token in re.findall(r"[a-z0-9]+", value) if len(token) >= 4 and token not in generic}
    chinese = set(re.findall(r"[\u4e00-\u9fff]{2,}", value))
    chinese -= {"上海", "杭州", "苏州", "南京", "宁波", "无锡", "合肥", "嘉兴", "南通", "深圳", "广州", "厦门", "北京", "成都", "大会", "峰会", "活动"}
    return latin | chinese


def _event_completeness(event: Event) -> tuple[int, int]:
    fields = (event.venue, event.organizer, event.reason, event.raw_excerpt if hasattr(event, "raw_excerpt") else "")
    populated = sum(bool(str(value or "").strip()) for value in fields)
    metadata_size = len(event.metadata or {})
    return populated, metadata_size


def _name_completeness(name: str) -> int:
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", name or ""))


def _event_type_rank(event_type: str) -> int:
    return {
        "开发者大会": 4,
        "峰会": 3,
        "展会": 3,
        "webinar": 2,
        "side_event": 2,
        "沙龙": 1,
        "沙龙·meetup": 1,
    }.get(str(event_type or "").strip(), 0)


def _preferred_tier(left: str, right: str) -> str:
    ranks = {"A": 4, "B": 3, "C": 2, "D": 1}
    return left if ranks.get(left, 0) >= ranks.get(right, 0) else right


def _score_history_for(event: Event) -> list[dict[str, Any]]:
    history = list(event.score_history or [])
    if not history:
        history = [{
            "timestamp": event.last_verified or event.first_seen,
            "acquisition_score": event.acquisition_score,
            "ecosystem_score": event.ecosystem_score,
        }]
    return history


def _combine_score_history(*events: Event) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        for entry in _score_history_for(event):
            key = repr(sorted(entry.items()))
            if key not in seen:
                combined.append(dict(entry))
                seen.add(key)
    return combined


def _merge_duplicate_pair(left: Event, right: Event) -> Event:
    richer, other = (right, left) if _event_completeness(right) > _event_completeness(left) else (left, right)
    merged = deepcopy(richer)
    merged_from = set(left.metadata.get("merged_from") or []) | set(right.metadata.get("merged_from") or [])
    merged_from.update([left.id, right.id])
    merged.metadata = {**other.metadata, **merged.metadata, "merged_from": sorted(merged_from)}
    name_source = max((left, right), key=lambda event: (_name_completeness(event.name), _event_completeness(event)))
    merged.name = name_source.name
    if not merged.name_en:
        merged.name_en = left.name_en or right.name_en
    event_type_source = max((left, right), key=lambda event: _event_type_rank(event.event_type))
    merged.event_type = event_type_source.event_type
    merged.acquisition_score = max(left.acquisition_score, right.acquisition_score)
    merged.ecosystem_score = max(left.ecosystem_score, right.ecosystem_score)
    merged.tier = _preferred_tier(left.tier, right.tier)
    merged.score_history = _combine_score_history(left, right)
    if other.first_seen and (not merged.first_seen or other.first_seen < merged.first_seen):
        merged.first_seen = other.first_seen
    return merged


def _collapse_duplicates(events: Iterable[Event]) -> list[Event]:
    collapsed: list[Event] = []
    for event in events:
        match_index = next((index for index, current in enumerate(collapsed) if same_event(current, event)), None)
        if match_index is None:
            collapsed.append(event)
        else:
            collapsed[match_index] = _merge_duplicate_pair(collapsed[match_index], event)
    return collapsed


def is_valid_candidate(row: dict[str, Any], scoring: dict[str, Any]) -> tuple[bool, str]:
    name = str(row.get("name") or row.get("raw_title") or "").strip()
    if not parse_iso(str(row.get("date_start") or "")):
        return False, "missing_or_unparseable_date"
    normalized = re.sub(r"\s+", " ", name.lower()).strip()
    blacklist = [str(value).lower().strip() for value in scoring.get("title_blacklist", [])]
    if any(value and (normalized == value or value in normalized) for value in blacklist):
        return False, "title_blacklist"
    compact_name = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]", "", name)
    short_title_allowlist = {"waic", "金投赏"}
    if len(compact_name) < 4 and compact_name.lower() not in short_title_allowlist:
        return False, "title_too_short"
    return True, ""


def prefilter_candidates(candidates: Iterable[dict[str, Any]], scoring: dict[str, Any], today: date | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    primary = str(scoring.get("primary_city", "上海"))
    nearby = set(scoring.get("nearby_cities", []))
    official = set(scoring.get("platform_official_sources", [])) | {"calendar-seed", "annual-ai-conferences"}
    kept: list[dict[str, Any]] = []
    counts = {"invalid": 0, "past": 0, "out_of_scope": 0, "overseas": 0}
    today = today or date.today()
    for row in candidates:
        valid, _reason = is_valid_candidate(row, scoring)
        if not valid:
            counts["invalid"] += 1
            continue
        start = parse_iso(str(row.get("date_start") or ""))
        if start and start < today:
            counts["past"] += 1
            continue
        city = str(row.get("city") or "")
        source = str(row.get("source") or row.get("source_id") or "")
        if city == "海外":
            counts["overseas"] += 1
            continue
        if city in {primary, "线上"} or city in nearby or source in official:
            kept.append(row)
            continue
        counts["out_of_scope"] += 1
    return kept, counts


def _seed_name_overlap(left: str, right: str) -> bool:
    """Match a calendar seed to a more specific confirmed title without broad fuzzy joins."""
    left_name = re.sub(r"20\d{2}", "", normalize_name(left))
    right_name = re.sub(r"20\d{2}", "", normalize_name(right))
    if not left_name or not right_name:
        return False
    shorter = min(left_name, right_name, key=len)
    return len(shorter) >= 4 and (left_name in right_name or right_name in left_name)


def _is_expected_seed(event: Event) -> bool:
    return event.status == "expected" or event.date_precision == "month" or event.source == "calendar-seed"


def _seed_match(left: Event, right: Event) -> bool:
    if left.city != right.city or _is_expected_seed(left) == _is_expected_seed(right):
        return False
    seed, exact = (left, right) if _is_expected_seed(left) else (right, left)
    if not _seed_name_overlap(seed.name, exact.name):
        return False
    seed_day, exact_day = parse_iso(seed.date_start), parse_iso(exact.date_start)
    if not seed_day or not exact_day:
        return False
    return seed_day.year == exact_day.year and (seed_day.month == exact_day.month or abs((seed_day - exact_day).days) <= 45)


def make_id(name: str, date_start: str, url: str = "") -> str:
    raw = f"{normalize_name(name)}|{date_start[:10]}|{url}".encode("utf-8")
    return "evt-" + hashlib.sha1(raw).hexdigest()[:12]


def same_event(left: Event, right: Event) -> bool:
    left_name, right_name = normalize_name(left.name), normalize_name(right.name)
    score = SequenceMatcher(None, left_name, right_name).ratio()
    if left.city != right.city:
        return False
    if left.url and right.url and left.url.rstrip("/") == right.url.rstrip("/") and score >= 0.78:
        return True
    ldate, rdate = parse_iso(left.date_start), parse_iso(right.date_start)
    if not ldate or not rdate or abs((ldate - rdate).days) > 1:
        return False
    if left_name == right_name or score >= 0.94:
        return True
    return bool(significant_name_tokens(left.name) & significant_name_tokens(right.name))


def classify_tier(acquisition: float, ecosystem: float, city: str, event_type: str, scoring: dict[str, Any]) -> str:
    raw = max(acquisition, ecosystem)
    rules = scoring.get("tier_rules", {"A": 8, "B": 6, "C": 4})
    if raw >= float(rules.get("A", 8)):
        return "A"
    if raw >= float(rules.get("B", 6)):
        return "B"
    if float(rules.get("C", 4)) <= acquisition < float(rules.get("B", 6)) and float(rules.get("C", 4)) <= ecosystem < float(rules.get("B", 6)):
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
    # Scoring reasons describe buyers and channels, not event location evidence.
    event.city = infer_city(event.name, event.venue, "", event.city)
    event.audience_side = str(raw.get("audience_side") or event.audience_side)
    event.scale_hint = str(raw.get("scale_hint") or event.scale_hint)
    event.format = str(raw.get("format") or event.format)
    if scoring.get("score_profile"):
        event.metadata["score_profile"] = str(scoring["score_profile"])
    corrections = scoring.get("corrections", {})
    acquisition = event.acquisition_score
    ecosystem = event.ecosystem_score
    raw_scores = {"acquisition_score": acquisition, "ecosystem_score": ecosystem}
    applied_corrections: list[str] = []
    if event.audience_side == "supply":
        capped = min(acquisition, float(corrections.get("supply_acquisition_cap", 4)))
        if capped != acquisition:
            applied_corrections.append("supply_acquisition_cap")
        acquisition = capped
    scale_numbers = [int(value) for value in re.findall(r"\d+", event.scale_hint)]
    scale_label = event.scale_hint.strip().lower()
    explicitly_small = scale_label in {"small", "small_salon", "small_open", "小型", "小规模"}
    is_small = (any(value < 30 for value in scale_numbers) if "展商" in event.scale_hint else any(value < 200 for value in scale_numbers)) if scale_numbers else explicitly_small
    if is_small and event.format == "open":
        penalty = abs(float(corrections.get("small_open", -2)))
        acquisition -= penalty
        ecosystem -= penalty
        applied_corrections.append("small_open")
    if raw.get("is_training") or re.search(r"课程|培训|训练营|实训|公开课|体验课|workshop|bootcamp|training", f"{event.name} {event.reason}", flags=re.IGNORECASE):
        capped = min(acquisition, float(corrections.get("pure_training_acquisition_cap", 3)))
        if capped != acquisition:
            applied_corrections.append("pure_training_acquisition_cap")
        acquisition = capped
    salon_types = {"沙龙", "沙龙·meetup"}
    salon_scale_unknown = not scale_numbers or event.scale_hint.strip().lower() in {"", "unknown", "未知"}
    salon_under_200 = bool(scale_numbers) and max(scale_numbers) < 200
    if event.event_type in salon_types and event.format == "open" and (salon_scale_unknown or salon_under_200 or explicitly_small):
        cap = float(corrections.get("small_open_salon_cap", 7))
        capped_acquisition = min(acquisition, cap)
        capped_ecosystem = min(ecosystem, cap)
        if (capped_acquisition, capped_ecosystem) != (acquisition, ecosystem):
            applied_corrections.append("small_open_salon_cap")
        acquisition, ecosystem = capped_acquisition, capped_ecosystem
    nearby = set(scoring.get("nearby_cities", ["杭州", "苏州", "南京", "宁波", "无锡", "合肥", "嘉兴", "南通"]))
    if event.city in nearby:
        penalty = abs(float(corrections.get("nearby_city", -1)))
        acquisition -= penalty
        ecosystem -= penalty
        applied_corrections.append("nearby_city")
    elif event.city not in {scoring.get("primary_city", "上海"), "Shanghai", "线上"}:
        penalty = abs(float(corrections.get("other_domestic", -2)))
        acquisition -= penalty
        ecosystem -= penalty
        applied_corrections.append("other_domestic")
        event.metadata["web_only"] = True
    if event.format == "invite_only":
        penalty = abs(float(corrections.get("invitation_only", -1)))
        acquisition -= penalty
        ecosystem -= penalty
        applied_corrections.append("invitation_only")
        event.metadata["invitation_note"] = "值得托关系"
    event.acquisition_score = max(0.0, min(10.0, acquisition))
    event.ecosystem_score = max(0.0, min(10.0, ecosystem))
    if applied_corrections:
        event.metadata["score_audit"] = {
            "raw": raw_scores,
            "applied": applied_corrections,
            "final": {
                "acquisition_score": event.acquisition_score,
                "ecosystem_score": event.ecosystem_score,
            },
        }
    event.tier = classify_tier(event.acquisition_score, event.ecosystem_score, event.city, event.event_type, scoring)
    event.action = raw.get("action") or choose_action(event)
    # Series status is produced only by _collapse_series. LLM output is a hint, not state.
    event.is_series = bool(event.metadata.get("series_rule"))
    event.occurrences = list(event.metadata.get("occurrences") or event.occurrences)
    if not event.score_history:
        event.score_history = [{"timestamp": now, "acquisition_score": event.acquisition_score, "ecosystem_score": event.ecosystem_score}]
    return event


def _collapse_series(candidates: Iterable[Event]) -> list[Event]:
    rows = list(candidates)
    groups: dict[tuple[str, str], list[Event]] = {}
    for event in rows:
        groups.setdefault((normalize_name(event.name), event.city), []).append(event)
    singles: list[Event] = []
    for grouped in groups.values():
        dates = sorted({value for item in grouped for value in [item.date_start, *item.occurrences] if value})
        if len(grouped) == 1 or len(dates) < 2:
            occurrence_dates = sorted(set(grouped[0].occurrences))
            if len(occurrence_dates) >= 2:
                grouped[0].is_series = True
                grouped[0].occurrences = occurrence_dates
                grouped[0].metadata = {
                    **grouped[0].metadata,
                    "series_rule": True,
                    "is_series": True,
                    "occurrences": occurrence_dates,
                }
            else:
                grouped[0].is_series = False
                grouped[0].occurrences = []
                grouped[0].metadata.pop("is_series", None)
                grouped[0].metadata.pop("series_rule", None)
            singles.extend(grouped)
            continue
        grouped.sort(key=lambda item: (item.date_start, item.name))
        series = deepcopy(grouped[0])
        series.name = _strip_series_issue(series.name).strip()
        series.date_start = dates[0]
        series.date_end = dates[-1]
        series.is_series = True
        series.occurrences = dates
        series.metadata = {
            **series.metadata,
            "series_rule": True,
            "is_series": True,
            "occurrences": dates,
            "merged_event_ids": [item.id for item in grouped],
        }
        singles.append(series)
    return singles


def _preserve_series_state(existing: Event, candidate: Event) -> Event:
    dates = sorted({value for event in (existing, candidate) for value in [event.date_start, *event.occurrences] if value})
    candidate.name = _strip_series_issue(candidate.name).strip()
    candidate.date_start = dates[0]
    candidate.date_end = dates[-1]
    candidate.is_series = True
    candidate.occurrences = dates
    candidate.metadata = {
        **candidate.metadata,
        "series_rule": True,
        "is_series": True,
        "occurrences": dates,
    }
    return candidate


def _collapse_seed_matches(events: Iterable[Event]) -> list[Event]:
    rows = list(events)
    removed: set[str] = set()
    for seed in rows:
        if seed.id in removed or not _is_expected_seed(seed):
            continue
        exact = next((event for event in rows if event.id not in removed and event.id != seed.id and not _is_expected_seed(event) and _seed_match(seed, event)), None)
        if exact is None:
            continue
        exact.metadata = {
            **exact.metadata,
            "seed_id": seed.metadata.get("calendar_id") or seed.metadata.get("seed_id") or seed.id,
        }
        exact.status = "active"
        exact.date_precision = "day"
        if seed.first_seen and (not exact.first_seen or seed.first_seen < exact.first_seen):
            exact.first_seen = seed.first_seen
        removed.add(seed.id)
    return [event for event in rows if event.id not in removed]


def _scale_at_least(value: str, minimum: int) -> bool:
    return any(int(number.replace(",", "")) >= minimum for number in re.findall(r"\d[\d,]*", value or ""))


def apply_side_event_links(events: Iterable[Event]) -> list[Event]:
    rows = list(events)
    conferences: list[Event] = []
    for event in rows:
        event.side_event_opportunity = False
        event.related_to = ""
        start, end = parse_iso(event.date_start), parse_iso(event.date_end)
        multi_day = bool(start and end and end > start and not event.is_series)
        platform_official = bool(event.metadata.get("platform_official"))
        eligible_tier = event.tier == "A"
        if eligible_tier and (multi_day or platform_official or _scale_at_least(event.scale_hint, 1000)):
            event.side_event_opportunity = True
            conferences.append(event)
    for event in rows:
        if event in conferences or event.related_to:
            continue
        event_day = parse_iso(event.date_start)
        if not event_day:
            continue
        related = next(
            (
                conference
                for conference in conferences
                if conference.city == event.city
                and (conference_day := parse_iso(conference.date_start))
                and abs((event_day - conference_day).days) <= 2
            ),
            None,
        )
        if related:
            event.related_to = related.id
    return rows


def merge_events(existing: Iterable[Event], candidates: Iterable[Event], city_scope: set[str], scoring: dict[str, Any]) -> tuple[list[Event], dict[str, int]]:
    cleaned_existing: list[Event] = []
    salon_cap = float(scoring.get("corrections", {}).get("small_open_salon_cap", 7))
    training_cap = float(scoring.get("corrections", {}).get("pure_training_acquisition_cap", 3))
    for original in existing:
        event = deepcopy(original)
        valid, _reason = is_valid_candidate(event.to_dict(), scoring)
        allowed_existing = event.city in city_scope or (event.metadata.get("web_only") and event.tier == "A") or event.event_type == "webinar"
        if not valid or not allowed_existing:
            cleaned_existing.append(event)
            continue
        if re.search(r"课程|培训|训练营|实训|公开课|体验课|workshop|bootcamp|training", f"{event.name} {event.reason}", flags=re.IGNORECASE):
            event.acquisition_score = min(event.acquisition_score, training_cap)
        scale_numbers = [int(value) for value in re.findall(r"\d+", event.scale_hint)]
        small_salon = not scale_numbers or max(scale_numbers) < 200
        if event.event_type in {"沙龙", "沙龙·meetup"} and event.format == "open" and small_salon:
            event.acquisition_score = min(event.acquisition_score, salon_cap)
            event.ecosystem_score = min(event.ecosystem_score, salon_cap)
        event.tier = classify_tier(event.acquisition_score, event.ecosystem_score, event.city, event.event_type, scoring)
        cleaned_existing.append(event)
    merged = _collapse_duplicates(_collapse_seed_matches(_collapse_series(cleaned_existing)))
    stats = {"new": 0, "changed": 0, "unchanged": 0, "dropped": 0, "invalid": 0}
    mutable_fields = ("name", "name_en", "date_start", "date_end", "city", "venue", "organizer", "url", "ticket_price", "register_deadline", "event_type", "acquisition_score", "ecosystem_score", "tier", "action", "reason", "source", "date_precision", "audience_side", "scale_hint", "format", "is_series", "occurrences", "side_event_opportunity", "related_to", "needs_review", "metadata")
    for candidate in _collapse_seed_matches(_collapse_series(candidates)):
        candidate_valid, _candidate_reason = is_valid_candidate(candidate.to_dict(), scoring)
        if not candidate_valid:
            stats["invalid"] += 1
            continue
        errors = validate_event(candidate)
        allowed_city = candidate.city in city_scope or (candidate.metadata.get("web_only") and candidate.tier == "A") or candidate.event_type == "webinar"
        if errors or not allowed_city or candidate.tier == "D":
            stats["invalid" if errors else "dropped"] += 1
            continue
        match = next((event for event in merged if same_event(event, candidate)), None)
        seed_join = False
        if match is None:
            match = next((event for event in merged if _seed_match(event, candidate)), None)
            seed_join = match is not None
        if seed_join and match is not None:
            seed = match if _is_expected_seed(match) else candidate
            candidate.metadata = {
                **candidate.metadata,
                "seed_id": seed.metadata.get("calendar_id") or seed.metadata.get("seed_id") or seed.id,
            }
            if _is_expected_seed(candidate) and not _is_expected_seed(match):
                # A late seed must enrich the confirmed record, never overwrite its exact date.
                preserved = deepcopy(match)
                preserved.metadata = {**preserved.metadata, "seed_id": candidate.metadata["seed_id"]}
                candidate = preserved
            else:
                candidate.status = "active"
                candidate.date_precision = "day"
        if match is None:
            merged.append(candidate)
            stats["new"] += 1
            continue
        if normalize_name(match.name) == normalize_name(candidate.name) and (match.is_series or candidate.is_series):
            candidate = _preserve_series_state(match, candidate)
        if match.source != candidate.source or normalize_name(match.name) != normalize_name(candidate.name):
            candidate = _merge_duplicate_pair(match, candidate)
        changed = any(getattr(match, field) != getattr(candidate, field) for field in mutable_fields)
        if changed:
            previous_acquisition = match.acquisition_score
            previous_ecosystem = match.ecosystem_score
            history = list(match.score_history or _score_history_for(match))
            current_score = {"timestamp": candidate.last_verified, "acquisition_score": candidate.acquisition_score, "ecosystem_score": candidate.ecosystem_score}
            if (previous_acquisition, previous_ecosystem) != (candidate.acquisition_score, candidate.ecosystem_score):
                history.append(current_score)
            needs_review = abs(previous_acquisition - candidate.acquisition_score) > 2 or abs(previous_ecosystem - candidate.ecosystem_score) > 2
            first_seen = match.first_seen
            for field in mutable_fields:
                setattr(match, field, getattr(candidate, field))
            match.first_seen = first_seen
            match.last_verified = candidate.last_verified
            match.status = "changed"
            if seed_join:
                match.status = "active"
            match.score_history = history
            match.needs_review = match.needs_review or candidate.needs_review or needs_review
            stats["changed"] += 1
        else:
            match.last_verified = candidate.last_verified
            stats["unchanged"] += 1
    merged = _collapse_duplicates(_collapse_seed_matches(_collapse_series(merged)))
    merged = apply_side_event_links(merged)
    merged.sort(key=lambda item: (item.date_start or "9999-12-31", item.name))
    return merged, stats


def upcoming(events: Iterable[Event], today: date, days: int = 120) -> list[Event]:
    end = today + timedelta(days=days)
    return [event for event in events if (start := parse_iso(event.date_start)) and today <= start <= end and event.status != "cancelled"]
