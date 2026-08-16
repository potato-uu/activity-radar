from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .config import RadarConfig
from .io import read_json, read_jsonl, write_json, write_jsonl
from .push import build_push_for_config, send_via_hermes
from .render import render_timeline
from .research import discover_and_score, now_iso
from .rules import merge_events, prepare_event
from .schema import Event


def root_from_args(args: argparse.Namespace) -> Path:
    return Path(args.root).expanduser().resolve() if args.root else Path.cwd()


def load_events(config: RadarConfig) -> list[Event]:
    return [Event.from_dict(row) for row in read_jsonl(config.events_path)]


def write_events(config: RadarConfig, events: list[Event]) -> None:
    write_jsonl(config.events_path, [event.to_dict() for event in events])


def update_source_health(config: RadarConfig, research_stats: dict[str, object]) -> list[str]:
    health = read_json(config.source_health_path, {})
    hits = set(research_stats.get("source_hits", []))
    errors = set(research_stats.get("source_errors", []))
    stale: list[str] = []
    for source in config.sources:
        source_id = source["id"]
        row = health.setdefault(source_id, {"no_hit_runs": 0, "last_hit": None, "last_scanned": None, "status": "active"})
        row["last_scanned"] = now_iso()
        if source_id in hits:
            row["last_hit"] = row["last_scanned"]
            row["no_hit_runs"] = 0
        else:
            row["no_hit_runs"] = int(row.get("no_hit_runs", 0)) + 1
        if source_id in errors:
            row["last_error"] = row["last_scanned"]
        if row["no_hit_runs"] >= 4:
            stale.append(source_id)
    write_json(config.source_health_path, health)
    return stale


def cmd_run(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    fixture = Path(args.fixture).expanduser() if args.fixture else None
    candidates, research_stats = discover_and_score(config, fixture=fixture, live=args.live)
    stale_sources = update_source_health(config, research_stats)
    events, merge_stats = merge_events(load_events(config), candidates, config.city_scope, config.scoring)
    write_events(config, events)
    generated_at = now_iso()
    render_timeline(events, config.site_path, generated_at)
    message = build_push_for_config(events, config, date.today())
    push_path = config.root / "data/push-latest.txt"
    push_result = send_via_hermes(message, config.push_target, dry_run=not args.send, output=push_path)
    summary = {"research": research_stats, "stale_sources": stale_sources, "merge": merge_stats, "events": len(events), "site": str(config.site_path), "push": push_result}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    events = load_events(config)
    render_timeline(events, config.site_path, now_iso())
    print(config.site_path)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    message = build_push_for_config(load_events(config), config)
    result = send_via_hermes(message, config.push_target, dry_run=not args.send, output=config.root / "data/push-latest.txt")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    now = now_iso()
    raw = {
        "name": args.name or "待补录活动",
        "name_en": args.name_en or "",
        "date_start": args.date_start or "",
        "date_end": args.date_end or args.date_start or "",
        "city": "上海",
        "venue": args.venue or "",
        "organizer": args.organizer or "",
        "url": args.url,
        "ticket_price": args.ticket_price or "",
        "register_deadline": args.register_deadline or "",
        "event_type": args.event_type,
        "acquisition_score": args.acquisition_score,
        "ecosystem_score": args.ecosystem_score,
        "reason": args.reason or "人工补录，待下一次研究运行核验。",
        "action": args.action,
    }
    if not args.name or not args.date_start:
        print("radar add requires --name and --date-start for a deterministic manual record", file=sys.stderr)
        return 2
    event = prepare_event(raw, "manual", now, config.scoring)
    events, stats = merge_events(load_events(config), [event], config.city_scope, config.scoring)
    write_events(config, events)
    print(json.dumps({"event": event.to_dict(), "merge": stats}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    rows = fixture.get("events", fixture) if isinstance(fixture, dict) else fixture
    as_of = date.fromisoformat(args.as_of)
    events = [prepare_event(row, row.get("source", "fixture"), args.as_of, config.scoring) for row in rows]
    google = [event for event in events if "google" in (event.name + event.name_en).lower() and event.ecosystem_score >= 8 and event.tier in {"A", "B"}]
    result = {"as_of": args.as_of, "captured": len(google) > 0, "matches": [event.to_dict() for event in google], "as_of_is_before_event": all(date.fromisoformat(event.date_start) >= as_of for event in google)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["captured"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="radar", description="Shanghai BD activity radar")
    parser.add_argument("--root", help="project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="research, merge, render, and create a push sample")
    run.add_argument("--fixture", help="fixture JSON, avoiding live API calls")
    run.add_argument("--live", action="store_true", help="call the configured Responses API")
    run.add_argument("--send", action="store_true", help="send through Hermes; default is dry-run")
    run.set_defaults(func=cmd_run)

    render = sub.add_parser("render", help="render the current events.jsonl")
    render.set_defaults(func=cmd_render)

    push = sub.add_parser("push", help="build the four-section push")
    push.add_argument("--send", action="store_true", help="send through Hermes; default is dry-run")
    push.set_defaults(func=cmd_push)

    add = sub.add_parser("add", help="manually add one event")
    add.add_argument("url")
    add.add_argument("--name")
    add.add_argument("--name-en")
    add.add_argument("--date-start")
    add.add_argument("--date-end")
    add.add_argument("--venue")
    add.add_argument("--organizer")
    add.add_argument("--ticket-price")
    add.add_argument("--register-deadline")
    add.add_argument("--event-type", default="峰会")
    add.add_argument("--acquisition-score", type=float, default=6)
    add.add_argument("--ecosystem-score", type=float, default=6)
    add.add_argument("--action", default="watch_content")
    add.add_argument("--reason")
    add.set_defaults(func=cmd_add)

    backtest = sub.add_parser("backtest", help="run the Google Shanghai acceptance backtest")
    backtest.add_argument("--fixture", required=True)
    backtest.add_argument("--as-of", default="2026-07-01")
    backtest.set_defaults(func=cmd_backtest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"radar: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
