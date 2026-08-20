from __future__ import annotations

import shutil
import subprocess
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import RadarConfig
from .io import append_jsonl, read_json, read_jsonl
from .schema import Event, parse_iso
from .rules import is_valid_candidate


PUSH_MAX_CHARS = 1800
TIMELINE_URL = "https://potato-uu.github.io/activity-radar/"


def eligible(event: Event, config: RadarConfig) -> bool:
    return event.tier in {"A", "B"} and _valid_for_push(event, config)


def _valid_for_push(event: Event, config: RadarConfig) -> bool:
    if not is_valid_candidate(event.to_dict(), config.scoring)[0]:
        return False
    return config.webinar_in_push or event.event_type.lower() != "webinar"


def _shanghai_time(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Asia/Shanghai"))


def auto_mode(now: datetime | None = None) -> str | None:
    """Return the scheduled mode for the current Shanghai hour, or None to skip."""
    local = _shanghai_time(now)
    if local.weekday() == 6 and local.hour == 18:
        return "full"
    if local.weekday() == 2 and local.hour == 10:
        return "delta"
    return None


def has_successful_auto_run(config: RadarConfig, now: datetime, mode: str) -> bool:
    day = _shanghai_time(now).date().isoformat()
    marker = config.root / "data/push-history" / f"{day}-{mode}.success"
    if marker.exists():
        return True
    for row in read_jsonl(config.root / "logs/push.jsonl"):
        if row.get("kind") == "auto_push" and row.get("status") == "sent" and row.get("mode") == mode and row.get("shanghai_date") == day:
            return True
    return False


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _last_artifact_time(config: RadarConfig, mode: str) -> datetime | None:
    """Read the last locally written sample without touching external state."""
    history_dir = config.root / "data/push-history"
    latest: datetime | None = None
    for path in history_dir.glob(f"*-{mode}.txt"):
        match = re.match(r"^(\d{8}T\d{6}Z)-", path.name)
        if not match:
            continue
        try:
            value = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if latest is None or value > latest:
            latest = value
    return latest


def _seen_since(event: Event, boundary: datetime | None, today: date, fallback_days: int) -> bool:
    seen = _parse_datetime(event.first_seen)
    if seen is None:
        return False
    if boundary is None:
        return seen.date() >= today - timedelta(days=fallback_days)
    # Date-only records have no reliable time-of-day; compare them by Shanghai date.
    if "T" not in event.first_seen:
        return seen.date() >= boundary.astimezone(ZoneInfo("Asia/Shanghai")).date()
    return seen >= boundary


def _date_label(event: Event) -> str:
    date_label = event.date_start or "日期待定"
    if event.date_precision == "month" and event.date_start:
        parsed = parse_iso(event.date_start)
        date_label = f"{parsed.year}年{parsed.month}月" if parsed else date_label
    if event.status == "expected" or event.date_precision == "month":
        date_label += "（日期待官宣）"
    if event.is_series and len(event.occurrences) >= 2:
        dates = "、".join(event.occurrences)
        date_label += f"（系列：{dates}）"
    return date_label


def _type_city_format(event: Event) -> str:
    type_label = "沙龙" if event.event_type in {"沙龙", "沙龙·meetup"} else event.event_type
    format_label = {"open": "公开", "closed_door": "闭门", "invite_only": "邀约"}.get(event.format, event.format or "未知")
    return f"{type_label}·{event.city}·{format_label}"


def _compact_line(event: Event) -> str:
    warning = " ⚠️" if event.needs_review else ""
    return (
        f"- {_date_label(event)}｜{event.name}｜Tier {event.tier}{warning}｜"
        f"获客 {event.acquisition_score:g} / 资源 {event.ecosystem_score:g}｜{_type_city_format(event)}"
    )


def _reason_text(reason: str) -> str:
    text = " ".join(part.strip() for part in str(reason or "").splitlines() if part.strip())
    if not text:
        return "理由待补充。"
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", text) if part.strip()]
    if len(parts) < 2:
        parts = [part.strip() for part in re.split(r"[；;]\s*", text) if part.strip()]
    return " ".join(parts[:2])


def _discovery_block(event: Event) -> str:
    return f"{_compact_line(event)}\n  理由：{_reason_text(event.reason)}"


def _scale_at_least(event: Event, threshold: int) -> bool:
    value: Any = event.scale_hint
    if isinstance(value, (int, float)):
        return value >= threshold
    raw = str(value or "")
    match = re.search(r"(?<!\d)(\d[\d,]*)", raw)
    return bool(match and int(match.group(1).replace(",", "")) >= threshold)


def _is_platform_official(event: Event) -> bool:
    return bool(event.metadata.get("platform_official"))


def _is_side_conference(event: Event) -> bool:
    if event.tier != "A" or not event.side_event_opportunity:
        return False
    start, end = parse_iso(event.date_start), parse_iso(event.date_end)
    multi_day = bool(start and end and end > start)
    return multi_day or _is_platform_official(event) or _scale_at_least(event, 1000)


def _health_line(config: RadarConfig, today: date) -> str:
    health = read_json(config.source_health_path, {})
    stale: list[str] = []
    observing: list[str] = []
    unavailable: list[str] = []
    for source_id, row in health.items():
        if row.get("last_result") in {"error", "blocked", "unavailable"}:
            unavailable.append(source_id)
            continue
        first = _parse_datetime(row.get("first_scanned") or row.get("last_scanned"))
        last_hit = _parse_datetime(row.get("last_hit"))
        first_age = (today - first.date()).days if first else 0
        hit_age = (today - last_hit.date()).days if last_hit else None
        if first and first_age >= 28 and (hit_age is None or hit_age >= 28):
            stale.append(source_id)
        elif row.get("last_result") == "empty" and first and first_age < 28:
            observing.append(source_id)
    stale.sort()
    observing.sort()
    unavailable = sorted(set(unavailable))
    parts: list[str] = []
    parts.append("连续 4 周无 hit：" + (", ".join(stale) if stale else "无"))
    if observing:
        parts.append(f"新源观察中：{len(observing)} 个")
    if unavailable:
        parts.append("unavailable/异常：" + ", ".join(unavailable))
    return "源健康度：" + "；".join(parts)


def _pack_units(units: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit in units:
        if not unit:
            continue
        candidate = unit if not current else f"{current}\n\n{unit}"
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = unit
        elif len(unit) <= max_chars:
            current = candidate
        else:
            # Keep paragraph and item boundaries where possible, then hard-split only oversized lines.
            if current:
                chunks.append(current)
                current = ""
            for line in unit.splitlines(keepends=False):
                if len(line) <= max_chars:
                    if current and len(current) + 1 + len(line) > max_chars:
                        chunks.append(current)
                        current = line
                    else:
                        current = line if not current else f"{current}\n{line}"
                else:
                    if current:
                        chunks.append(current)
                        current = ""
                    for offset in range(0, len(line), max_chars):
                        part = line[offset : offset + max_chars]
                        if len(part) == max_chars:
                            chunks.append(part)
                        else:
                            current = part
    if current:
        chunks.append(current)
    return chunks or [""]


def split_message(message: str, max_chars: int = PUSH_MAX_CHARS) -> list[str]:
    """Split on blank-line paragraphs, preserving a hard upper bound per block."""
    units = [unit.strip() for unit in message.split("\n\n") if unit.strip()]
    chunks = _pack_units(units, max_chars)
    for _ in range(3):
        count = len(chunks)
        prefix_len = len(f"（{count}/{count}）\n")
        adjusted = _pack_units(units, max_chars - prefix_len)
        if len(adjusted) == count:
            chunks = adjusted
            break
        chunks = adjusted
    count = len(chunks)
    return [f"（{index}/{count}）\n{chunk}" for index, chunk in enumerate(chunks, 1)]


def build_push(events: list[Event], config: RadarConfig, today: date | None = None, mode: str = "full") -> str:
    return build_push_for_config(events, config, today, mode=mode)


def build_push_for_config(events: list[Event], config: RadarConfig, today: date | None = None, *, mode: str = "full") -> str:
    today = today or date.today()
    if mode not in {"full", "delta"}:
        raise ValueError("push mode must be full or delta")
    valid_events = [event for event in events if _valid_for_push(event, config)]
    eligible_events = [event for event in valid_events if event.tier in {"A", "B"}]
    boundary = _last_artifact_time(config, "full")
    if mode == "full":
        recent = [event for event in valid_events if _seen_since(event, boundary, today, 7)]
        new_events = [event for event in recent if event.tier in {"A", "B"}]
        recent_c_count = sum(1 for event in recent if event.tier == "C")
    else:
        recent = [event for event in eligible_events if _seen_since(event, boundary, today, 3)]
        new_events = [event for event in recent if event.status not in {"cancelled"}] + [
            event for event in eligible_events if event.status in {"changed", "cancelled"} and event not in recent
        ]
        recent_c_count = 0
    window_end = today + timedelta(days=28)

    def in_future_window(event: Event) -> bool:
        start = parse_iso(event.date_start)
        if not start:
            return False
        if event.date_precision == "month" or event.status == "expected":
            if start.month == 12:
                month_end = date(start.year, 12, 31)
            else:
                month_end = date(start.year, start.month + 1, 1) - timedelta(days=1)
            return month_end >= today and start <= window_end
        return today <= start <= window_end

    future = [event for event in eligible_events if in_future_window(event)]
    alerts = [event for event in eligible_events if (deadline := parse_iso(event.register_deadline)) and today <= deadline <= today + timedelta(days=7)]
    side_conferences = []
    for event in valid_events:
        if not _is_side_conference(event):
            continue
        side_conferences.append(event)
    side_lines: list[str] = []
    for conference in side_conferences:
        related = [event.name for event in valid_events if event.related_to == conference.id and event.id != conference.id and event.status != "cancelled"]
        visible = related[:5]
        suffix = "、".join(visible) if visible else "周边局待发现"
        if len(related) > 5:
            suffix += f"等 {len(related) - 5} 个"
        side_lines.append(f"- {conference.name}（{_date_label(conference)}）：{suffix}")

    if mode == "delta" and not new_events and not alerts:
        new_text = "- 本周三无新增，本周无变更，雷达正常，下次周日 18:00"
    elif mode == "delta":
        new_text = "\n".join(_compact_line(event) for event in new_events) or "- 无"
    else:
        blocks = [_discovery_block(event) for event in new_events[:12]]
        if mode == "full" and len(new_events) > 12:
            blocks.append(f"- 另有 {len(new_events) - 12} 条新发现见网页")
        if mode == "full" and recent_c_count:
            blocks.append(f"- 另有 C 级 {recent_c_count} 条，见网页")
        new_text = "\n\n".join(blocks) or "- 无"
    future_text = "\n".join(_compact_line(event) for event in future) or "- 无"
    alert_text = "\n".join(_compact_line(event) for event in alerts) or "- 无"
    if mode == "delta":
        return "\n".join([
            "上海 BD 活动雷达",
            "模式：delta",
            "",
            "1. 本周以来新增/变更/取消",
            new_text,
            "",
            "2. 报名/购票截止 <= 7 天",
            alert_text,
            "",
            f"完整时间轴：{TIMELINE_URL}",
        ])
    sections = [
        "上海 BD 活动雷达",
        f"模式：{mode}",
        "",
        "1. 本周新发现" if mode == "full" else "1. 本周以来新增/变更/取消",
        new_text,
        "",
        "2. 未来 4 周 Tier A/B",
        future_text,
        "",
        "3. 报名/购票截止 <= 7 天",
        alert_text,
    ]
    sections.extend(["", "4. side event 机会", "\n".join(side_lines) or "- 无"])
    sections.extend(["", f"5. {_health_line(config, today)}", "", f"完整时间轴：{TIMELINE_URL}"])
    return "\n".join(sections)


def send_via_hermes(
    message: str,
    target: str,
    *,
    dry_run: bool = True,
    output: Path | None = None,
    log_path: Path | None = None,
    sleep_fn: Any = None,
) -> dict[str, object]:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(message, encoding="utf-8")
    if dry_run:
        result = {"status": "dry_run", "target": target, "chars": len(message), "chunks": len(split_message(message))}
        if log_path:
            append_jsonl(log_path, {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **result})
        return result
    executable = shutil.which("hermes")
    if not executable:
        fallback = Path.home() / ".hermes/hermes-agent/venv/bin/hermes"
        executable = str(fallback) if fallback.exists() else None
    if not executable:
        error = "Hermes executable not found; checked PATH and ~/.hermes/hermes-agent/venv/bin/hermes"
        if log_path:
            append_jsonl(log_path, {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "failed", "target": target, "chars": len(message), "error": error})
        raise RuntimeError(error)
    chunks = split_message(message)
    sleep_fn = sleep_fn or time.sleep
    for index, chunk in enumerate(chunks, 1):
        result = subprocess.run([executable, "send", "--to", target, "--file", "-", "--json"], input=chunk, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            if log_path:
                append_jsonl(log_path, {
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "status": "failed",
                    "target": target,
                    "chars": len(message),
                    "chunk": index,
                    "chunk_count": len(chunks),
                    "returncode": result.returncode,
                    "error": result.stderr.strip()[:500],
                })
            raise RuntimeError(f"Hermes send failed ({result.returncode}) on chunk {index}/{len(chunks)}: {result.stderr.strip()}")
        if index < len(chunks):
            sleep_fn(35)
    response = {"status": "sent", "target": target, "chars": len(message), "chunks": len(chunks), "chunk_chars": [len(chunk) for chunk in chunks], "result": result.stdout.strip()}
    if log_path:
        append_jsonl(log_path, {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **response})
    return response


def record_auto_success(config: RadarConfig, mode: str, now: datetime | None = None) -> None:
    local = _shanghai_time(now)
    marker = config.root / "data/push-history" / f"{local.date().isoformat()}-{mode}.success"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("status=sent\n", encoding="utf-8")
    append_jsonl(config.root / "logs/push.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": "auto_push",
        "status": "sent",
        "mode": mode,
        "shanghai_date": local.date().isoformat(),
    })


def write_push_artifacts(config: RadarConfig, message: str, mode: str) -> dict[str, str]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    latest = config.root / "data/push-latest.txt"
    history = config.root / "data/push-history" / f"{timestamp}-{mode}.txt"
    latest.parent.mkdir(parents=True, exist_ok=True)
    history.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(message, encoding="utf-8")
    history.write_text(message, encoding="utf-8")
    return {"latest": str(latest), "history": str(history)}
