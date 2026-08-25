from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .config import RadarConfig
from .io import append_jsonl, read_json, read_jsonl, write_json, write_jsonl
from .normalization import clean_source_title, infer_city
from .push import (
    auto_delivery_paths,
    auto_mode,
    build_push_for_config,
    has_successful_auto_run,
    record_auto_success,
    research_freshness,
    send_via_hermes,
    write_push_artifacts,
)
from .render import render_timeline
from .research import discover_and_score, now_iso, rescore_active_events, score_pending_candidates
from .rules import classify_tier, merge_events, prepare_event
from .schema import Event


def root_from_args(args: argparse.Namespace) -> Path:
    return Path(args.root).expanduser().resolve() if args.root else Path.cwd()


def load_events(config: RadarConfig) -> list[Event]:
    return [Event.from_dict(row) for row in read_jsonl(config.events_path)]


def write_events(config: RadarConfig, events: list[Event]) -> None:
    write_jsonl(config.events_path, [event.to_dict() for event in events])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_git_sha(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


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
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    candidates, research_stats = discover_and_score(config, fixture=fixture, live=args.live, source_ids=source_ids, as_of=as_of)
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
    if args.live:
        write_json(config.root / "data/research-meta.json", {
            "completed_at": _utc_now().isoformat(timespec="seconds"),
            "git_sha": _read_git_sha(config.root),
            "event_count": len(events),
            "mode": args.push_mode,
        })
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
    now = _utc_now()
    pull_failed = os.getenv("RADAR_GIT_PULL_FAILED") == "1"
    dependency_update_failed = os.getenv("RADAR_DEPENDENCY_UPDATE_FAILED") == "1"
    if pull_failed:
        append_jsonl(config.root / "logs/push.jsonl", {
            "timestamp": now.isoformat(timespec="seconds"),
            "kind": "pull_failed",
            "status": "failed",
            "error": "git pull --ff-only failed; using local data",
        })
    if dependency_update_failed:
        append_jsonl(config.root / "logs/push.jsonl", {
            "timestamp": now.isoformat(timespec="seconds"),
            "kind": "dependency_update_failed",
            "status": "failed",
            "error": "pyproject dependency update failed; using old environment",
        })
    mode = args.mode
    if args.auto:
        mode = auto_mode(now)
        if mode is None:
            print("skip")
            return 0
        if has_successful_auto_run(config, now, mode):
            print("skip")
            return 0
        outbox_path, success_marker = auto_delivery_paths(config, now, mode)
        if outbox_path.exists() and args.send:
            result = send_via_hermes(
                "",
                config.push_target,
                dry_run=False,
                log_path=config.root / "logs/push.jsonl",
                outbox_path=outbox_path,
                success_marker=success_marker,
            )
            record_auto_success(config, mode, now)
            result["artifacts"] = {"outbox": str(outbox_path), "resumed": True}
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        freshness, freshness_warning = research_freshness(config, now, mode)
        if freshness == "waiting":
            append_jsonl(config.root / "logs/push.jsonl", {
                "timestamp": now.isoformat(timespec="seconds"),
                "kind": "auto_skip",
                "reason": "waiting_fresh_data",
                "mode": mode,
            })
            print("skip")
            return 0
    else:
        freshness_warning = None
        outbox_path = None
        success_marker = None
    if args.chatgpt_ads_rerank and (args.auto or mode != "full"):
        print("--chatgpt-ads-rerank requires --mode full without --auto", file=sys.stderr)
        return 2
    message = build_push_for_config(
        load_events(config),
        config,
        mode=mode,
        chatgpt_ads_rerank=args.chatgpt_ads_rerank,
    )
    if pull_failed:
        message = message.rstrip() + "\n\n⚠️ 数据未更新（git pull 失败）"
    if freshness_warning:
        message = message.rstrip() + f"\n\n{freshness_warning}"
    artifact_mode = "chatgpt-ads-rerank" if args.chatgpt_ads_rerank else mode
    artifacts = write_push_artifacts(config, message, artifact_mode)
    result = send_via_hermes(
        message,
        config.push_target,
        dry_run=not args.send,
        output=config.root / "data/push-latest.txt",
        log_path=config.root / "logs/push.jsonl",
        outbox_path=outbox_path,
        success_marker=success_marker,
    )
    if args.auto and result.get("status") == "sent":
        record_auto_success(config, mode, now)
    result["artifacts"] = artifacts
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    if args.active:
        events, stats = rescore_active_events(config, load_events(config))
        write_events(config, events)
        render_timeline(events, config.site_path, now_iso(), config.scoring)
        print(json.dumps({"score": stats, "events": len(events)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if stats.get("scoring_result") in {"hit", "empty"} else 1
    candidates, stats = score_pending_candidates(config)
    events, merge_stats = merge_events(load_events(config), candidates, config.city_scope, config.scoring)
    write_events(config, events)
    render_timeline(events, config.site_path, now_iso(), config.scoring)
    print(json.dumps({"score": stats, "merge": merge_stats, "events": len(events)}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if stats.get("scoring_result") != "unavailable" else 1


def cmd_migrate(args: argparse.Namespace) -> int:
    config = RadarConfig.load(root_from_args(args))
    if args.backfill_audit:
        return _backfill_score_audits(config)
    if not args.clean_names:
        print("radar migrate requires --clean-names or --backfill-audit", file=sys.stderr)
        return 2
    rows = read_jsonl(config.events_path)
    migrated: list[dict[str, object]] = []
    changes: list[dict[str, str]] = []
    for original in rows:
        row = dict(original)
        old_name = str(row.get("name") or "")
        old_city = str(row.get("city") or "")
        new_name = clean_source_title(old_name, str(row.get("source") or ""))
        new_city = infer_city(
            old_name,
            str(row.get("venue") or ""),
            str(row.get("reason") or ""),
            old_city,
        )
        row["name"] = new_name
        row["city"] = new_city
        migrated.append(row)
        if (new_name, new_city) != (old_name, old_city):
            changes.append({
                "id": str(row.get("id") or ""),
                "name_before": old_name,
                "name_after": new_name,
                "city_before": old_city,
                "city_after": new_city,
            })
    if changes:
        write_jsonl(config.events_path, migrated)
    result = {
        "kind": "migrate_clean_names",
        "status": "success",
        "event_count": len(rows),
        "changed_count": len(changes),
        "changes": changes,
    }
    append_jsonl(config.root / "logs/migrate.jsonl", {"timestamp": now_iso(), **result})
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _backfill_score_audits(config: RadarConfig) -> int:
    rows = read_jsonl(config.events_path)
    migrated: list[dict[str, object]] = []
    changes: list[dict[str, object]] = []
    score_changed_count = 0
    for original in rows:
        row, changed, score_changed = _backfill_score_audit(original, config.scoring)
        migrated.append(row)
        if changed:
            audit = dict(row.get("metadata", {})).get("score_audit", {})
            changes.append({
                "id": str(row.get("id") or ""),
                "caps_applied": list(audit.get("caps_applied", [])),
                "score_changed": score_changed,
            })
            score_changed_count += int(score_changed)
    if changes:
        write_jsonl(config.events_path, migrated)
    result = {
        "kind": "migrate_backfill_audit",
        "status": "success",
        "event_count": len(rows),
        "changed_count": len(changes),
        "score_changed_count": score_changed_count,
        "changes": changes,
    }
    append_jsonl(config.root / "logs/migrate.jsonl", {"timestamp": now_iso(), **result})
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _backfill_score_audit(original: dict[str, object], scoring: dict[str, object]) -> tuple[dict[str, object], bool, bool]:
    row = dict(original)
    metadata = dict(row.get("metadata") or {})
    if "score_audit" in metadata:
        return row, False, False

    corrections = dict(scoring.get("corrections", {}))
    acquisition_before = float(row.get("acquisition_score") or 0)
    ecosystem_before = float(row.get("ecosystem_score") or 0)
    acquisition = acquisition_before
    ecosystem = ecosystem_before
    caps_applied: list[str] = []

    if str(row.get("audience_side") or "") == "supply":
        capped = min(acquisition, float(corrections.get("supply_acquisition_cap", 4)))
        if capped != acquisition:
            caps_applied.append("supply_acquisition_cap")
        acquisition = capped

    event_type = str(row.get("event_type") or "").strip().lower()
    if re.search(r"课程|培训|训练营|实训|公开课|体验课|workshop|bootcamp|training", event_type, flags=re.IGNORECASE):
        capped = min(acquisition, float(corrections.get("pure_training_acquisition_cap", 3)))
        if capped != acquisition:
            caps_applied.append("pure_training_acquisition_cap")
        acquisition = capped

    scale_hint = str(row.get("scale_hint") or "")
    scale_numbers = [int(value) for value in re.findall(r"\d+", scale_hint)]
    scale_label = scale_hint.strip().lower()
    explicitly_small = scale_label in {"small", "small_salon", "small_open", "小型", "小规模"}
    salon_scale_unknown = not scale_numbers or scale_label in {"", "unknown", "未知"}
    salon_under_200 = bool(scale_numbers) and max(scale_numbers) < 200
    if event_type in {"沙龙", "沙龙·meetup"} and str(row.get("format") or "") == "open" and (salon_scale_unknown or salon_under_200 or explicitly_small):
        cap = float(corrections.get("small_open_salon_cap", 7))
        capped_acquisition = min(acquisition, cap)
        capped_ecosystem = min(ecosystem, cap)
        if (capped_acquisition, capped_ecosystem) != (acquisition, ecosystem):
            caps_applied.append("small_open_salon_cap")
        acquisition, ecosystem = capped_acquisition, capped_ecosystem

    clamped_acquisition = max(0.0, min(10.0, acquisition))
    clamped_ecosystem = max(0.0, min(10.0, ecosystem))
    if (clamped_acquisition, clamped_ecosystem) != (acquisition, ecosystem):
        caps_applied.append("score_clamp_0_10")
    acquisition, ecosystem = clamped_acquisition, clamped_ecosystem

    row["acquisition_score"] = acquisition
    row["ecosystem_score"] = ecosystem
    row["tier"] = classify_tier(acquisition, ecosystem, str(row.get("city") or ""), event_type, scoring)
    metadata["score_audit"] = {
        "backfilled": True,
        "caps_applied": caps_applied,
        "final": {"acquisition_score": acquisition, "ecosystem_score": ecosystem},
    }
    row["metadata"] = metadata
    score_changed = (acquisition, ecosystem) != (acquisition_before, ecosystem_before)
    return row, True, score_changed


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
    run.add_argument("--as-of", help="fixed YYYY-MM-DD anchor for deterministic fixture runs")
    run.add_argument("--push-mode", choices=["full", "delta"], default="full")
    run.set_defaults(func=cmd_run)

    render = sub.add_parser("render", help="render the current events.jsonl")
    render.set_defaults(func=cmd_render)

    push = sub.add_parser("push", help="build the four-section push")
    push.add_argument("--send", action="store_true", help="send through Hermes; default is dry-run")
    push.add_argument("--mode", choices=["full", "delta"], default="full")
    push.add_argument("--auto", action="store_true", help="select full/delta from the current Asia/Shanghai time")
    push.add_argument("--chatgpt-ads-rerank", action="store_true", help="build the special ChatGPT Ads score-sorted full push")
    push.set_defaults(func=cmd_push)

    score = sub.add_parser("score", help="score persisted candidates")
    score_mode = score.add_mutually_exclusive_group(required=True)
    score_mode.add_argument("--pending", action="store_true", help="score data/candidates-unscored.jsonl and merge it into events")
    score_mode.add_argument("--active", action="store_true", help="re-score current active/expected/changed events not yet on the configured score profile")
    score.set_defaults(func=cmd_score)

    migrate = sub.add_parser("migrate", help="run an idempotent data migration")
    migrate.add_argument("--clean-names", action="store_true", help="clean stored event names and re-infer cities")
    migrate.add_argument("--backfill-audit", action="store_true", help="backfill score audit using idempotent caps only")
    migrate.set_defaults(func=cmd_migrate)

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
