from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .config import RadarConfig
from .io import read_json, read_jsonl, write_json, write_jsonl
from .push import (
    auto_mode,
    build_push_for_config,
    has_successful_auto_run,
    record_auto_success,
    send_via_hermes,
    write_push_artifacts,
)
from .render import render_timeline
from .research import discover_and_score, now_iso, score_pending_candidates
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
    scanned = set(research_stats.get("source_ids", []))
    error_details = dict(research_stats.get("source_error_details", {}))
    error_messages = dict(research_stats.get("source_error_messages", {}))
    candidate_counts = dict(research_stats.get("source_candidate_counts", {}))
    empty_reasons = dict(research_stats.get("source_empty_reasons", {}))
    stale: list[str] = []
    for source in config.sources:
        source_id = source["id"]
        if scanned and source_id not in scanned:
            continue
        row = health.setdefault(source_id, {"no_hit_runs": 0, "last_hit": None, "last_scanned": None, "status": "active"})
        was_scanned = bool(row.get("last_scanned"))
        row["scan_count"] = int(row.get("scan_count", 1 if was_scanned else 0)) + 1
        row["last_scanned"] = now_iso()
        row.setdefault("first_scanned", row["last_scanned"])
        if source_id in hits:
            row["last_hit"] = row["last_scanned"]
            row["no_hit_runs"] = 0
            row["last_result"] = "hit"
            row["reason"] = f"{int(candidate_counts.get(source_id, 1))} candidates"
            row.pop("last_error_kind", None)
        elif source_id in errors:
            row["last_result"] = error_details.get(source_id, "error")
            row["last_error_kind"] = row["last_result"]
            row["reason"] = error_messages.get(source_id) or row["last_result"]
        else:
            row["no_hit_runs"] = int(row.get("no_hit_runs", 0)) + 1
            row["last_result"] = "empty"
            row["reason"] = empty_reasons.get(source_id, "no candidates in the 120-day window")
            row.pop("last_error_kind", None)
        if source_id in errors:
            row["last_error"] = row["last_scanned"]
        if row["no_hit_runs"] >= 4:
            stale.append(source_id)
    write_json(config.source_health_path, health)
    return stale


def cmd_run(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    fixture = Path(args.fixture).expanduser() if args.fixture else None
    source_ids = [item.strip() for item in (args.sources or "").split(",") if item.strip()] or None
    candidates, research_stats = discover_and_score(config, fixture=fixture, live=args.live, source_ids=source_ids)
    stale_sources = update_source_health(config, research_stats)
    events, merge_stats = merge_events(load_events(config), candidates, config.city_scope, config.scoring)
    write_events(config, events)
    generated_at = now_iso()
    render_timeline(events, config.site_path, generated_at, config.scoring)
    message = build_push_for_config(events, config, mode=args.push_mode)
    push_path = config.root / "data/push-latest.txt"
    artifacts = write_push_artifacts(config, message, args.push_mode)
    push_result = send_via_hermes(message, config.push_target, dry_run=not args.send, output=push_path, log_path=config.root / "logs/push.jsonl")
    push_result["artifacts"] = artifacts
    summary = {"research": research_stats, "stale_sources": stale_sources, "merge": merge_stats, "events": len(events), "site": str(config.site_path), "push": push_result}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    events = load_events(config)
    render_timeline(events, config.site_path, now_iso(), config.scoring)
    print(config.site_path)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    now = datetime.now(timezone.utc)
    mode = args.mode
    if args.auto:
        mode = auto_mode(now)
        if mode is None:
            print("skip")
            return 0
        if has_successful_auto_run(config, now, mode):
            print("skip")
            return 0
    message = build_push_for_config(load_events(config), config, mode=mode)
    artifacts = write_push_artifacts(config, message, mode)
    result = send_via_hermes(message, config.push_target, dry_run=not args.send, output=config.root / "data/push-latest.txt", log_path=config.root / "logs/push.jsonl")
    if args.auto and result.get("status") == "sent":
        record_auto_success(config, mode, now)
    result["artifacts"] = artifacts
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    if not args.pending:
        print("radar score currently requires --pending", file=sys.stderr)
        return 2
    candidates, stats = score_pending_candidates(config)
    events, merge_stats = merge_events(load_events(config), candidates, config.city_scope, config.scoring)
    write_events(config, events)
    render_timeline(events, config.site_path, now_iso(), config.scoring)
    print(json.dumps({"score": stats, "merge": merge_stats, "events": len(events)}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if stats.get("scoring_result") != "unavailable" else 1


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
    as_of = date.fromisoformat(args.as_of)
    if args.live_source:
        events, stats = discover_and_score(config, live=True, source_ids=[args.live_source], as_of=as_of)
        google = [event for event in events if "google" in (event.name + event.name_en).lower() and event.ecosystem_score >= 8 and event.tier in {"A", "B"}]
        result = {"as_of": args.as_of, "source": args.live_source, "captured": len(google) > 0, "matches": [event.to_dict() for event in google], "research": stats, "as_of_is_before_event": all(date.fromisoformat(event.date_start) >= as_of for event in google)}
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["captured"] else 1
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    rows = fixture.get("events", fixture) if isinstance(fixture, dict) else fixture
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
    run.add_argument("--sources", help="comma-separated source ids")
    run.add_argument("--push-mode", choices=["full", "delta"], default="full")
    run.set_defaults(func=cmd_run)

    render = sub.add_parser("render", help="render the current events.jsonl")
    render.set_defaults(func=cmd_render)

    push = sub.add_parser("push", help="build the four-section push")
    push.add_argument("--send", action="store_true", help="send through Hermes; default is dry-run")
    push.add_argument("--mode", choices=["full", "delta"], default="full")
    push.add_argument("--auto", action="store_true", help="select full/delta from the current Asia/Shanghai time")
    push.set_defaults(func=cmd_push)

    score = sub.add_parser("score", help="score persisted candidates")
    score.add_argument("--pending", action="store_true", help="score data/candidates-unscored.jsonl and merge it into events")
    score.set_defaults(func=cmd_score)

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
    backtest.add_argument("--fixture")
    backtest.add_argument("--live-source", help="run the named live adapter instead of a fixture")
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
