from __future__ import annotations

import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import RadarConfig
from .io import append_jsonl, read_json, read_jsonl
from .schema import Event, parse_iso
from .rules import is_valid_candidate


def eligible(event: Event, config: RadarConfig) -> bool:
    if event.tier not in {"A", "B"}:
        return False
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


def build_push(events: list[Event], config: RadarConfig, today: date | None = None, mode: str = "full") -> str:
    return build_push_for_config(events, config, today, mode=mode)


def build_push_for_config(events: list[Event], config: RadarConfig, today: date | None = None, *, mode: str = "full") -> str:
    today = today or date.today()
    if mode not in {"full", "delta"}:
        raise ValueError("push mode must be full or delta")
    eligible_events = [event for event in events if eligible(event, config)]
    delta_start = today - timedelta(days=3 if mode == "delta" else 7)
    new_events = [event for event in eligible_events if (event.first_seen[:10] and event.first_seen[:10] >= delta_start.isoformat()) or event.status in {"changed", "cancelled"}]
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
    health = read_json(config.source_health_path, {})
    stale = [source_id for source_id, row in health.items() if int(row.get("no_hit_runs", 0)) >= 4]
    new_empty = [source_id for source_id, row in health.items() if row.get("last_result") == "empty" and int(row.get("scan_count", 0)) == 1 and not row.get("last_hit")]
    unavailable = [source_id for source_id, row in health.items() if row.get("last_result") in {"unavailable", "blocked", "error", "timeout"}]
    source_health = "源健康度：" + ("连续 4 周无 hit：" + ", ".join(stale) if stale else "本周未发现连续 4 周无 hit 的源")
    if new_empty:
        source_health += "；新源尚无命中：" + ", ".join(new_empty)
    if unavailable:
        source_health += "；unavailable/异常：" + ", ".join(unavailable)

    def line(event: Event) -> str:
        date_label = event.date_start or "日期待定"
        if event.date_precision == "month" and event.date_start:
            parsed = parse_iso(event.date_start)
            date_label = f"{parsed.year}年{parsed.month}月" if parsed else date_label
        if event.status == "expected" or event.date_precision == "month":
            date_label += "（日期待官宣）"
        if len(event.occurrences) >= 2:
            dates = "、".join(event.occurrences or [event.date_start])
            date_label += f"（系列：{dates}）"
        warning = " ⚠️" if event.needs_review else ""
        type_label = "沙龙" if event.event_type in {"沙龙", "沙龙·meetup"} else event.event_type
        format_label = {"open": "公开", "closed_door": "闭门", "invite_only": "邀约"}.get(event.format, event.format or "未知")
        return (
            f"- {date_label}｜{event.name}｜Tier {event.tier}{warning}｜获客 {event.acquisition_score:g} / 资源 {event.ecosystem_score:g}\n"
            f"  {type_label}·{event.city}·{format_label}\n"
            f"  {event.reason}"
        )

    side_conferences = []
    for event in events:
        if not event.side_event_opportunity or event.tier not in {"A", "B"}:
            continue
        start, end = parse_iso(event.date_start), parse_iso(event.date_end)
        multi_day = bool(start and end and end > start)
        if event.tier == "A" or multi_day:
            side_conferences.append(event)
    side_lines: list[str] = []
    for conference in side_conferences:
        related = [event.name for event in events if event.related_to == conference.id]
        side_lines.append(f"- {conference.name}（{conference.date_start}）" + ("：" + "、".join(related) if related else "：周边局待发现"))

    if mode == "delta" and not new_events and not alerts:
        new_text = "- 本周三无新增，本周无变更，雷达正常，下次周日 18:00"
    else:
        new_text = "\n".join(line(event) for event in new_events) or "- 无"
    sections = [
        "上海 BD 活动雷达",
        f"模式：{mode}",
        "",
        "1. 本周新发现" if mode == "full" else "1. 本周以来新增/变更/取消",
        new_text,
        "",
        "2. 未来 4 周 Tier A/B",
        "\n".join(line(event) for event in future) or "- 无",
        "",
        "3. 报名/购票截止 <= 7 天",
        "\n".join(line(event) for event in alerts) or "- 无",
    ]
    if mode == "full":
        sections.extend(["", "4. side event 机会", "\n".join(side_lines) or "- 无"])
    sections.extend(["", f"{5 if mode == 'full' else 4}. {source_health}"])
    return "\n".join(sections)


def send_via_hermes(message: str, target: str, *, dry_run: bool = True, output: Path | None = None, log_path: Path | None = None) -> dict[str, object]:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(message, encoding="utf-8")
    if dry_run:
        result = {"status": "dry_run", "target": target, "chars": len(message)}
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
    result = subprocess.run([executable, "send", "--to", target, "--file", "-", "--json"], input=message, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        if log_path:
            append_jsonl(log_path, {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "failed", "target": target, "chars": len(message), "returncode": result.returncode, "error": result.stderr.strip()[:500]})
        raise RuntimeError(f"Hermes send failed ({result.returncode}): {result.stderr.strip()}")
    response = {"status": "sent", "target": target, "chars": len(message), "result": result.stdout.strip()}
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
