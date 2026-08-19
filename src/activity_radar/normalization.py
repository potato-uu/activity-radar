from __future__ import annotations

import re
from datetime import date
from typing import Any

from .adapters.base import RawCandidate


CITY_ALIASES = {
    "Shanghai": "上海",
    "Hangzhou": "杭州",
    "Suzhou": "苏州",
    "Nanjing": "南京",
    "Ningbo": "宁波",
    "Wuxi": "无锡",
    "Hefei": "合肥",
    "Jiaxing": "嘉兴",
    "Nantong": "南通",
    "Online": "线上",
    "Beijing": "北京",
    "Shenzhen": "深圳",
    "Guangzhou": "广州",
    "Xiamen": "厦门",
    "Chengdu": "成都",
    "Wuhan": "武汉",
    "Chongqing": "重庆",
    "Qingdao": "青岛",
    "Tianjin": "天津",
    "Xi'an": "西安",
}
DOMESTIC_CITIES = (
    "上海", "杭州", "苏州", "南京", "宁波", "无锡", "合肥", "嘉兴", "南通",
    "深圳", "广州", "厦门", "北京", "成都", "武汉", "重庆", "青岛", "天津", "西安",
)
KNOWN_CITIES = DOMESTIC_CITIES
DISTRICT_ALIASES = {
    "徐汇区": "上海", "黄浦区": "上海", "静安区": "上海", "浦东新区": "上海",
    "浦东": "上海", "杨浦区": "上海", "虹桥": "上海", "Xuhui District": "上海",
    "Xu Hui Qu": "上海", "Pu Dong Xin Qu": "上海", "Pudong": "上海",
}
OVERSEAS_HINTS = ("海外", "美国", "英国", "日本", "新加坡", "香港", "台湾", "硅谷", "London", "Tokyo", "Singapore", "New York", "San Francisco")


def _year_for(month: int, day: int, anchor: date) -> int:
    candidate = date(anchor.year, month, day)
    return anchor.year + 1 if candidate < anchor and (anchor - candidate).days > 180 else anchor.year


def _parse_end_date(value: str, start: date, tail: str) -> date:
    end_match = re.search(
        r"(?:-|~|～|—|至)\s*(?:(20\d{2})[./-]|(20\d{2})年)?"
        r"(?:(\d{1,2})月|(?:(\d{1,2})[./-]))?(\d{1,2})日?",
        tail,
    )
    if not end_match:
        return start
    year = int(end_match.group(1) or end_match.group(2) or start.year)
    month = int(end_match.group(3) or end_match.group(4) or start.month)
    return date(year, month, int(end_match.group(5)))


def parse_date_text(text: str, anchor: date) -> tuple[str, str, str]:
    value = str(text or "").strip()
    explicit_chinese = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日?", value)
    if explicit_chinese:
        try:
            start = date(int(explicit_chinese.group(1)), int(explicit_chinese.group(2)), int(explicit_chinese.group(3)))
            end = _parse_end_date(value, start, value[explicit_chinese.end():])
        except ValueError:
            return "", "", "unknown"
        return start.isoformat(), end.isoformat(), "day"
    iso = re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", value)
    if iso:
        try:
            start = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            end = _parse_end_date(value, start, value[iso.end():])
        except ValueError:
            return "", "", "unknown"
        return start.isoformat(), end.isoformat(), "day"

    chinese = re.search(r"(\d{1,2})月(\d{1,2})日?", value)
    if chinese:
        month, day = int(chinese.group(1)), int(chinese.group(2))
        try:
            year = _year_for(month, day, anchor)
            start = date(year, month, day)
            end = _parse_end_date(value, start, value[chinese.end():])
        except ValueError:
            return "", "", "unknown"
        return start.isoformat(), end.isoformat(), "day"

    dotted = re.search(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)", value)
    if dotted:
        month, day = int(dotted.group(1)), int(dotted.group(2))
        try:
            parsed = date(_year_for(month, day, anchor), month, day)
        except ValueError:
            return "", "", "unknown"
        return parsed.isoformat(), parsed.isoformat(), "day"

    compact = re.search(r"(?<!\d)(\d{2})(\d{2})(?!\d)", value)
    if compact:
        month, day = int(compact.group(1)), int(compact.group(2))
        try:
            parsed = date(_year_for(month, day, anchor), month, day)
        except ValueError:
            return "", "", "unknown"
        return parsed.isoformat(), parsed.isoformat(), "day"
    return "", "", "unknown"


def infer_city(raw_title: str, venue: str = "", excerpt: str = "", fallback: str = "") -> str:
    title_text = f"{raw_title} {venue} {excerpt}".strip()
    for alias, city in DISTRICT_ALIASES.items():
        if alias.lower() in title_text.lower():
            return city
    for city in DOMESTIC_CITIES:
        if city in title_text:
            return city
    for english, city in CITY_ALIASES.items():
        if english.lower() in title_text.lower():
            return city
    if any(hint.lower() in title_text.lower() for hint in OVERSEAS_HINTS):
        return "海外"
    if fallback in DISTRICT_ALIASES:
        return DISTRICT_ALIASES[fallback]
    if fallback in CITY_ALIASES:
        return CITY_ALIASES[fallback]
    return fallback or "上海"


def _canonical_city(raw: RawCandidate) -> str:
    # Title/location text outranks a platform's default city (e.g. Shenzhen in a Shanghai feed).
    return infer_city(raw.raw_title, raw.venue, raw.raw_excerpt, raw.city)


def _has_explicit_year(text: str) -> bool:
    return bool(re.search(r"20\d{2}(?:年\d{1,2}月\d{1,2}日?|[./-]\d{1,2}[./-]\d{1,2})", text or ""))


def _clean_source_title(title: str, source_id: str) -> str:
    """Remove AMZ123 card suffixes while retaining the actual event name."""
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    if source_id != "amz123":
        return value
    value = re.sub(r"\s+(?:(?:[\u4e00-\u9fff]{2,8})省)?[\u4e00-\u9fff]{2,8}市\s*$", "", value)
    value = re.sub(r"\s+20\d{2}(?:年\d{1,2}月\d{1,2}日?|[./-]\d{1,2}[./-]\d{1,2})\s*$", "", value)
    return value.strip(" -|·")


def _event_type(raw: RawCandidate) -> str:
    value = f"{raw.event_type} {raw.raw_title} {raw.raw_excerpt}".lower()
    if "webinar" in value or "线上直播" in value or raw.city == "线上":
        return "webinar"
    if "side event" in value or "side_event" in value or "afterparty" in value or "after party" in value:
        return "side_event"
    if "展" in value or "expo" in value or "exhibition" in value:
        return "展会"
    if "meetup" in value or "交流活动" in value or "沙龙" in value or "闭门" in value or "路演" in value:
        return "沙龙·meetup"
    if "开发者" in value or "devfest" in value or "developer" in value or "黑客松" in value:
        return "开发者大会"
    return "峰会"


def normalize_raw_candidate(raw: RawCandidate, anchor: date) -> dict[str, Any]:
    start = raw.date_start
    end = raw.date_end or start
    precision = raw.date_precision
    date_text = " ".join((raw.raw_title, raw.raw_date_text, raw.venue, raw.raw_excerpt))
    parsed_start, parsed_end, parsed_precision = parse_date_text(date_text, anchor)
    if _has_explicit_year(date_text) or not start:
        start, end, precision = parsed_start, parsed_end, parsed_precision
    name = _clean_source_title(raw.raw_title, raw.source_id)
    metadata = dict(raw.metadata)
    if re.search(r"(?i)\bvol\.?\s*\d+\b|第\s*\d+\s*期|每周|每月", raw.raw_title):
        metadata["series_hint"] = True
    return {
        "name": name,
        "date_start": start,
        "date_end": end or start,
        "date_precision": precision,
        "city": _canonical_city(raw),
        "venue": raw.venue.strip(),
        "organizer": raw.organizer.strip(),
        "url": raw.url.strip(),
        "event_type": _event_type(raw),
        "source": raw.source_id,
        "status": raw.status,
        "raw_excerpt": raw.raw_excerpt[:600],
        "fetched_at": raw.fetched_at,
        "metadata": metadata,
    }


def normalize_candidate_row(row: dict[str, Any], anchor: date) -> dict[str, Any]:
    item = dict(row)
    original_name = str(item.get("name") or item.get("raw_title") or "")
    item["name"] = _clean_source_title(original_name, str(item.get("source") or item.get("source_id") or ""))
    start = str(item.get("date_start") or "")
    end = str(item.get("date_end") or start)
    precision = str(item.get("date_precision") or ("day" if start else "unknown"))
    date_text = " ".join(str(item.get(key) or "") for key in ("name", "raw_title", "raw_date_text", "venue", "raw_excerpt"))
    parsed_start, parsed_end, parsed_precision = parse_date_text(date_text, anchor)
    if _has_explicit_year(date_text) or not start:
        start, end, precision = parsed_start, parsed_end, parsed_precision
    item["date_start"] = start
    item["date_end"] = end or start
    item["date_precision"] = precision
    item["city"] = infer_city(
        str(item.get("name") or item.get("raw_title") or ""),
        str(item.get("venue") or ""),
        str(item.get("raw_excerpt") or ""),
        str(item.get("city") or ""),
    )
    return item
