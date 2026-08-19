from __future__ import annotations

import re
import urllib.parse
from datetime import date
from typing import Any

from .base import AdapterWindow, RawCandidate
from .http import RateLimitedHttpClient


PUBLIC_FIELDS = (
    "id,external_id,region_code,title,date,start_date,start_date_text,end_date,"
    "end_date_text,time,location,district,organizer,type,event_type,fee,summary,"
    "registration_mode,registration_closed_at,registration_referral_required,"
    "has_registration_action,has_source_action,image_url,curation_label,updated_at,status"
)


def _js_string(text: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}\s*:\s*(['\"])(.*?)\1", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"OnePilot public config is missing {key}")
    return match.group(2).strip()


def _iso_day(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text) else ""


def map_onepilot_row(row: dict[str, Any], fetched_at: str) -> RawCandidate:
    external_id = str(row.get("external_id") or row.get("id") or "").strip()
    start = _iso_day(row.get("start_date") or row.get("date"))
    end = _iso_day(row.get("end_date")) or start
    region = str(row.get("region_code") or "shanghai").lower()
    city = "线上" if region == "online" else "上海"
    location = str(row.get("location") or "").strip()
    if city == "上海" and not location:
        location = str(row.get("district") or "上海").strip()
    url = (
        f"https://onepilot.xin/events/{urllib.parse.quote(external_id, safe='')}/register"
        if external_id
        else "https://onepilot.xin/timeline/"
    )
    raw_date = str(row.get("start_date_text") or row.get("date") or start).strip()
    if row.get("end_date_text"):
        raw_date = f"{raw_date} - {row['end_date_text']}"
    excerpt = str(row.get("summary") or "").strip()[:600]
    return RawCandidate(
        source_id="onepilot",
        raw_title=str(row.get("title") or "").strip(),
        raw_date_text=raw_date,
        date_start=start,
        date_end=end,
        city=city,
        venue=location,
        organizer=str(row.get("organizer") or "").strip(),
        url=url,
        raw_excerpt=excerpt,
        event_type=str(row.get("event_type") or row.get("type") or "").strip(),
        fetched_at=fetched_at,
        date_precision="day" if start else "unknown",
        metadata={
            "external_id": external_id,
            "region_code": region,
            "fee": str(row.get("fee") or "").strip(),
            "registration_closed_at": str(row.get("registration_closed_at") or "").strip(),
            "registration_referral_required": bool(row.get("registration_referral_required")),
            "curation_label": str(row.get("curation_label") or "").strip(),
        },
    )


class OnePilotAdapter:
    def fetch(self, config: dict[str, Any], window: AdapterWindow) -> list[RawCandidate]:
        params = config.get("params") or {}
        client = RateLimitedHttpClient(
            interval_seconds=float(params.get("request_interval_seconds", 2)),
            timeout_seconds=int(params.get("timeout_seconds", 45)),
        )
        config_url = str(config.get("url") or "https://onepilot.xin/supabase-config.js")
        public_config = client.get_text(config_url)
        supabase_url = _js_string(public_config, "url").rstrip("/")
        anon_key = _js_string(public_config, "anonKey")
        query = urllib.parse.urlencode(
            {
                "select": PUBLIC_FIELDS,
                "status": "eq.published",
                "region_code": "in.(shanghai,online)",
                "order": "date.asc",
            },
            safe="(),.*",
        )
        endpoint = f"{supabase_url}/rest/v1/onepilot_public_events?{query}"
        rows = client.get_json(
            endpoint,
            headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
        )
        if not isinstance(rows, list):
            raise RuntimeError("OnePilot public event response was not a JSON array")
        from ..research import now_iso

        fetched_at = now_iso()
        candidates: list[RawCandidate] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate = map_onepilot_row(row, fetched_at)
            if not candidate.raw_title or not candidate.date_start:
                continue
            start = date.fromisoformat(candidate.date_start)
            if window.start <= start <= window.end:
                candidates.append(candidate)
        return candidates
