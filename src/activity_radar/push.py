from __future__ import annotations

import hashlib
import shutil
import subprocess
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import RadarConfig
from .io import append_jsonl, read_json, read_jsonl, write_json
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
    # >= instead of == so a missed exact hour (agent down, machine asleep) still
    # sends later the same day; has_successful_auto_run keeps it once per day.
    if local.weekday() == 6 and local.hour >= 18:
        return "full"
    if local.weekday() == 2 and local.hour >= 10:
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


def auto_delivery_paths(config: RadarConfig, now: datetime, mode: str) -> tuple[Path, Path]:
    day = _shanghai_time(now).date().isoformat()
    history = config.root / "data/push-history"
    return history / f"{day}-{mode}.outbox.json", history / f"{day}-{mode}.success"


def research_freshness(config: RadarConfig, now: datetime, mode: str) -> tuple[str, str | None]:
    """Classify scheduled research as fresh, waiting, or stale in Shanghai time."""
    local = _shanghai_time(now)
    meta = read_json(config.root / "data/research-meta.json", {})
    completed = _parse_datetime(str(meta.get("completed_at") or ""))
    completed_local = completed.astimezone(ZoneInfo("Asia/Shanghai")) if completed else None
    if completed_local and completed_local.date() == local.date():
        return "fresh", None

    cutoff_hour = 22 if mode == "full" else 14
    if local.hour < cutoff_hour:
        return "waiting", None
    if completed_local:
        return "stale", f"⚠️ 数据为 {completed_local.month}/{completed_local.day} 研究结果（今日研究未完成）"
    return "stale", "⚠️ 未找到研究结果日期（今日研究未完成）"


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


def _short_date(value: date) -> str:
    return f"{value.month}/{value.day}"


def _date_label(event: Event, today: date) -> str:
    parsed = parse_iso(event.date_start)
    if (event.status == "expected" or event.date_precision == "month") and parsed:
        label = f"{parsed.month}月（日期待官宣）"
    elif parsed:
        weekday = "一二三四五六日"[parsed.weekday()]
        label = f"{_short_date(parsed)} 周{weekday}"
    else:
        label = "日期待定"
    if event.is_series and len(event.occurrences) >= 2:
        occurrences = sorted(filter(None, (parse_iso(value) for value in event.occurrences)))
        upcoming = [value for value in occurrences if value >= today and (not parsed or value > parsed)]
        if not upcoming:
            upcoming = [value for value in occurrences if value >= today]
        next_date = upcoming[0] if upcoming else parsed
        if next_date:
            label += f"（每周，下一场 {_short_date(next_date)}）"
    return label


def _type_city_format(event: Event) -> str:
    type_label = "沙龙" if event.event_type in {"沙龙", "沙龙·meetup"} else event.event_type
    format_label = {"open": "公开", "closed_door": "闭门", "invite_only": "邀约"}.get(event.format, event.format or "未知")
    return f"{type_label}·{event.city}·{format_label}"


def _event_name(event: Event) -> str:
    return f"{event.name}{' ⚠️' if event.needs_review else ''}"


def _reason_text(reason: str) -> str:
    text = " ".join(part.strip() for part in str(reason or "").splitlines() if part.strip())
    if not text:
        return "理由待补充。"
    first = re.split(r"[。！？!?]|(?<!\d)\.(?=\s|$)", text, maxsplit=1)[0].strip()
    body = first.rstrip("。！？!?.,，；; ") or "理由待补充"
    if len(body) >= 40:
        clipped = body[:39]
        boundary = max(clipped.rfind(ch) for ch in "，、；：,;:")
        body = (clipped[:boundary] if boundary >= 15 else clipped).rstrip("。！？!?.,，、；;：: ")
    return f"{body}。"


def _link_line(event: Event) -> str:
    url = str(event.url or "").strip()
    return f"🔗 {url}" if url.lower().startswith(("http://", "https://")) else ""


def _is_rate_limit_error(error_text: str) -> bool:
    value = error_text.lower()
    return any(marker in value for marker in ("rate limit", "rate_limit", "cooldown", "too many requests", "429"))


def _number_label(index: int) -> str:
    return chr(0x2460 + index - 1) if index <= 20 else f"{index}."


def _a_event_block(event: Event, index: int, today: date) -> str:
    lines = [
        f"{_number_label(index)} {_date_label(event, today)}｜{_event_name(event)}",
        f"获客{event.acquisition_score:g} 资源{event.ecosystem_score:g}｜{_type_city_format(event)}",
        _reason_text(event.reason),
    ]
    if link := _link_line(event):
        lines.append(link)
    return "\n".join(lines)


def _two_line_event(event: Event, today: date) -> str:
    lines = [f"· {_date_label(event, today)}｜{_event_name(event)}｜获客{event.acquisition_score:g}"]
    if link := _link_line(event):
        lines.append(link)
    return "\n".join(lines)


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
    today = today or _shanghai_time().date()
    if mode not in {"full", "delta"}:
        raise ValueError("push mode must be full or delta")
    valid_events = [event for event in events if _valid_for_push(event, config)]
    eligible_events = [event for event in valid_events if event.tier in {"A", "B"}]
    boundary = _last_artifact_time(config, "full")
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

    def sort_key(event: Event) -> tuple[str, str]:
        return (event.date_start or "9999-12-31", event.name)

    def is_upcoming(event: Event) -> bool:
        start = parse_iso(event.date_start)
        end = parse_iso(event.date_end) or start
        if not start or not end:
            return False
        if event.date_precision == "month" or event.status == "expected":
            if start.month == 12:
                end = date(start.year, 12, 31)
            else:
                end = date(start.year, start.month + 1, 1) - timedelta(days=1)
        return end >= today

    active_eligible = [event for event in eligible_events if event.status != "cancelled"]
    alerts = sorted(
        [event for event in active_eligible if (deadline := parse_iso(event.register_deadline)) and today <= deadline <= today + timedelta(days=7)],
        key=lambda event: (event.register_deadline, *sort_key(event)),
    )
    timeline = f"🌐 完整时间轴 {TIMELINE_URL}"

    if mode == "delta":
        recent = [event for event in eligible_events if _seen_since(event, boundary, today, 3)]
        additions = sorted([event for event in recent if event.status not in {"changed", "cancelled"}], key=sort_key)

        def changed_since_full(event: Event) -> bool:
            changed_at = _parse_datetime(event.last_verified) or _parse_datetime(event.first_seen)
            if changed_at is None:
                return True
            if boundary is None:
                return changed_at.date() >= today - timedelta(days=3)
            return changed_at >= boundary

        changes = sorted([event for event in eligible_events if event.status == "changed" and changed_since_full(event)], key=sort_key)
        cancellations = sorted([event for event in eligible_events if event.status == "cancelled" and changed_since_full(event)], key=sort_key)
        header = f"📡 BD 活动雷达｜{today.month}/{today.day} 增量"
        if not additions and not changes and not cancellations and not alerts:
            return "\n\n".join([
                header,
                "本周三无新增，本周无变更，雷达正常，下次周日 18:00",
                timeline,
            ])
        sections = [header]
        for heading, rows in (("🆕 新增", additions), ("✏️ 变更", changes), ("❌ 取消", cancellations)):
            if rows:
                sections.append(f"{heading}\n\n" + "\n".join(_two_line_event(event, today) for event in rows))
        if alerts:
            sections.append("⏰ 报名截止 ≤7 天\n\n" + "\n".join(_two_line_event(event, today) for event in alerts))
        sections.append(timeline)
        return "\n\n".join(sections)

    a_events = sorted([event for event in active_eligible if event.tier == "A" and is_upcoming(event)], key=sort_key)
    b_events = sorted([event for event in active_eligible if event.tier == "B" and in_future_window(event)], key=sort_key)
    side_conferences = sorted([event for event in active_eligible if _is_side_conference(event) and in_future_window(event)], key=sort_key)
    a_text = "\n\n".join(_a_event_block(event, index, today) for index, event in enumerate(a_events, 1)) or "暂无"
    b_text = "\n".join(_two_line_event(event, today) for event in b_events) or "暂无"
    sections = [
        f"📡 BD 活动雷达｜{today.month}/{today.day} 全量",
        f"⭐ 必看（A 级）\n\n{a_text}",
        f"📅 值得排期（B 级·未来 4 周）\n\n{b_text}",
    ]
    if alerts:
        sections.append("⏰ 报名截止 ≤7 天\n\n" + "\n".join(_two_line_event(event, today) for event in alerts))
    if side_conferences:
        side_rows: list[str] = []
        for conference in side_conferences:
            side_rows.append(_two_line_event(conference, today))
            related = sorted(
                [event for event in active_eligible if event.related_to == conference.id and event.id != conference.id],
                key=sort_key,
            )
            side_rows.extend(_two_line_event(event, today) for event in related[:5])
            if len(related) > 5:
                side_rows.append(f"· 等 {len(related) - 5} 个周边局见时间轴")
        sections.append("🎪 side event 机会\n\n" + "\n".join(side_rows))
    health = _health_line(config, today).removeprefix("源健康度：")
    sections.append(f"{timeline}\n🩺 源健康：{health}")
    return "\n\n".join(sections)


def send_via_hermes(
    message: str,
    target: str,
    *,
    dry_run: bool = True,
    output: Path | None = None,
    log_path: Path | None = None,
    outbox_path: Path | None = None,
    success_marker: Path | None = None,
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
    outbox: dict[str, Any] | None = None
    if outbox_path and outbox_path.exists():
        outbox = read_json(outbox_path, {})
        chunks = [str(chunk) for chunk in outbox.get("chunks", [])]
        if not chunks:
            raise RuntimeError(f"Push outbox has no chunks: {outbox_path}")
        sent = {str(index): str(value) for index, value in dict(outbox.get("sent", {})).items()}
    else:
        chunks = split_message(message)
        sent = {}
        if outbox_path:
            outbox = {
                "chunks": chunks,
                "sent": sent,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            _write_outbox(outbox_path, outbox)
    message_id = next(iter(sent.values()), hashlib.sha256("\0".join(chunks).encode("utf-8")).hexdigest()[:16])
    sleep_fn = sleep_fn or time.sleep
    pending_indexes = [index for index in range(1, len(chunks) + 1) if str(index) not in sent]
    last_stdout = ""
    for pending_position, index in enumerate(pending_indexes):
        chunk = chunks[index - 1]
        # iLink rate-limits bursts of consecutive messages. Permanent failures
        # must fail immediately instead of sleeping through the retry window.
        for attempt, cooldown in enumerate((120, 300, None)):
            result = subprocess.run([executable, "send", "--to", target, "--file", "-", "--json"], input=chunk, text=True, capture_output=True, check=False)
            if result.returncode == 0:
                last_stdout = result.stdout.strip()
                break
            error_text = (result.stderr.strip() or result.stdout.strip())[:500]
            retry_cooldown = cooldown if _is_rate_limit_error(error_text) else None
            if log_path:
                append_jsonl(log_path, {
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "kind": "chunk_delivery",
                    "status": "retrying" if retry_cooldown else "failed",
                    "message_id": message_id,
                    "target": target,
                    "chars": len(message),
                    "chunk_chars": len(chunk),
                    "chunk": index,
                    "chunk_count": len(chunks),
                    "attempt": attempt + 1,
                    "returncode": result.returncode,
                    "error": error_text,
                })
            if retry_cooldown is None:
                raise RuntimeError(f"Hermes send failed ({result.returncode}) on chunk {index}/{len(chunks)} after {attempt + 1} attempts: {error_text}")
            sleep_fn(retry_cooldown)
        if log_path:
            append_jsonl(log_path, {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": "chunk_delivery",
                "status": "sent",
                "message_id": message_id,
                "target": target,
                "chars": len(message),
                "chunk_chars": len(chunk),
                "chunk": index,
                "chunk_count": len(chunks),
                "attempt": attempt + 1,
            })
        if outbox_path and outbox is not None:
            sent[str(index)] = message_id
            outbox["sent"] = sent
            _write_outbox(outbox_path, outbox)
        if pending_position < len(pending_indexes) - 1:
            sleep_fn(45)
    if success_marker:
        success_marker.parent.mkdir(parents=True, exist_ok=True)
        success_marker.write_text("status=sent\n", encoding="utf-8")
    if outbox_path:
        outbox_path.unlink(missing_ok=True)
    response = {"status": "sent", "message_id": message_id, "target": target, "chars": sum(len(chunk) for chunk in chunks), "chunks": len(chunks), "chunk_chars": [len(chunk) for chunk in chunks], "result": last_stdout}
    if log_path:
        append_jsonl(log_path, {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **response})
    return response


def _write_outbox(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_json(temporary, value)
    temporary.replace(path)


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
