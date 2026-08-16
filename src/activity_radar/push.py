from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path

from .config import RadarConfig
from .io import read_json
from .schema import Event, parse_iso


def eligible(event: Event, config: RadarConfig) -> bool:
    if event.tier not in {"A", "B"}:
        return False
    return config.webinar_in_push or event.event_type.lower() != "webinar"


def build_push(events: list[Event], config: RadarConfig, today: date | None = None) -> str:
    return build_push_for_config(events, config, today)


def build_push_for_config(events: list[Event], config: RadarConfig, today: date | None = None) -> str:
    today = today or date.today()
    eligible_events = [event for event in events if eligible(event, config)]
    new_events = [event for event in eligible_events if event.first_seen[:10] == today.isoformat() or event.status == "changed"]
    future = [event for event in eligible_events if (start := parse_iso(event.date_start)) and today <= start <= today + timedelta(days=28)]
    alerts = [event for event in eligible_events if (deadline := parse_iso(event.register_deadline)) and today <= deadline <= today + timedelta(days=7)]
    health = read_json(config.source_health_path, {})
    stale = [source_id for source_id, row in health.items() if int(row.get("no_hit_runs", 0)) >= 4]
    source_health = "源健康度：" + ("连续 4 周无 hit：" + ", ".join(stale) if stale else "本周未发现连续 4 周无 hit 的源")

    def line(event: Event) -> str:
        return f"- {event.date_start}｜{event.name}｜Tier {event.tier}｜获客 {event.acquisition_score:g} / 资源 {event.ecosystem_score:g}\n  {event.reason}"

    sections = ["上海 BD 活动雷达", "", "1. 本周新发现", "\n".join(line(event) for event in new_events) or "- 无", "", "2. 未来 4 周 Tier A/B", "\n".join(line(event) for event in future) or "- 无", "", "3. 报名/购票截止 <= 7 天", "\n".join(line(event) for event in alerts) or "- 无", "", f"4. {source_health}"]
    return "\n".join(sections)


def send_via_hermes(message: str, target: str, *, dry_run: bool = True, output: Path | None = None) -> dict[str, object]:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(message, encoding="utf-8")
    if dry_run:
        return {"status": "dry_run", "target": target, "chars": len(message)}
    result = subprocess.run(["hermes", "send", "--to", target, "--file", "-", "--json"], input=message, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Hermes send failed ({result.returncode}): {result.stderr.strip()}")
    return {"status": "sent", "target": target, "chars": len(message), "result": result.stdout.strip()}
