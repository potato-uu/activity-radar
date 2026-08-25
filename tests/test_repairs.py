import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from activity_radar import push as push_mod
from activity_radar.adapters.generic import extract_html_candidates
from activity_radar.cli import main, update_source_health
from activity_radar.config import RadarConfig
from activity_radar.io import read_json, read_jsonl
from activity_radar.provider import Usage
from activity_radar.push import auto_mode, build_push_for_config, has_successful_auto_run, record_auto_success, send_via_hermes, split_message
from activity_radar.render import render_timeline
from activity_radar.research import _score_and_prepare, _score_candidates_in_batches, rescore_active_events, score_prompt
from activity_radar.rules import is_valid_candidate, make_id, merge_events, prefilter_candidates, prepare_event
from activity_radar.schema import Event


ROOT = Path(__file__).resolve().parents[1]


def isolated_root(tmp_path: Path) -> Path:
    for name in ("config", "fixtures"):
        shutil.copytree(ROOT / name, tmp_path / name)
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data/events.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def test_config_loads_local_env_before_environment_resolution(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    (root / ".env").write_text("CODEX_BASE_URL=https://example.test/v1\nRADAR_MODEL=test-model\nRADAR_PUSH_TARGET=test-target\n", encoding="utf-8")
    monkeypatch.delenv("CODEX_BASE_URL", raising=False)
    monkeypatch.delenv("RADAR_MODEL", raising=False)
    monkeypatch.delenv("RADAR_PUSH_TARGET", raising=False)
    config = RadarConfig.load(root)
    assert config.base_url == "https://example.test/v1"
    assert config.model == "test-model"
    assert config.push_target == "test-target"


def test_scoring_batches_are_bounded_and_failed_batch_is_pending(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.discovery_concurrency = 3
    active = 0
    maximum = 0
    lock = threading.Lock()

    candidates = [{"name": f"Event {index}", "date_start": "2026-09-01", "url": f"https://e/{index}", "source": "test", "fetched_at": "now"} for index in range(31)]

    def fake_score(_config, batch):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        try:
            if batch[0]["name"] == "Event 15":
                raise RuntimeError("batch failed")
            return batch
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr("activity_radar.research._score_candidates", fake_score)
    scored, pending, failures = _score_candidates_in_batches(config, candidates)
    assert maximum <= 3
    assert len(scored) == 30
    assert len(pending) == 1
    assert len(failures) == 1
    assert pending[0]["name"] == "Event 15"
    assert pending[0]["unscorable_reason"] == "batch failed"


def test_poison_candidate_isolated_by_recursive_batch_split(tmp_path, monkeypatch):
    config = RadarConfig.load(isolated_root(tmp_path))
    candidates = [{"name": f"Event {index}", "date_start": "2026-09-01", "url": f"https://e/{index}"} for index in range(15)]

    def fake_score(_config, batch):
        if any(row["name"] == "Event 7" for row in batch):
            raise TimeoutError("504 timed out")
        return batch

    monkeypatch.setattr("activity_radar.research._score_candidates", fake_score)
    scored, pending, failures = _score_candidates_in_batches(config, candidates)
    assert len(scored) == 14
    assert [row["name"] for row in pending] == ["Event 7"]
    assert pending[0]["unscorable_reason"] == "504 timed out"
    assert failures == [{"batch": 0, "candidate_count": 1, "error": "504 timed out"}]


def test_single_poison_is_persisted_with_unscorable_reason(tmp_path, monkeypatch):
    config = RadarConfig.load(isolated_root(tmp_path))
    candidate = {"name": "Valid Poison Summit", "date_start": "2026-09-01", "city": "上海", "url": "https://e/poison", "source": "test"}
    monkeypatch.setattr("activity_radar.research._score_candidates", lambda _config, _batch: (_ for _ in ()).throw(TimeoutError("504 timed out")))
    events, result, _error, pending_count, _failures = _score_and_prepare(config, [candidate], "now")
    pending = read_jsonl(config.root / "data/candidates-unscored.jsonl")
    assert events == [] and result == "unavailable" and pending_count == 1
    assert pending[0]["unscorable_reason"] == "504 timed out"


def test_new_scoring_run_preserves_older_pending_candidates(tmp_path, monkeypatch):
    config = RadarConfig.load(isolated_root(tmp_path))
    old = {"name": "Older Pending Summit", "date_start": "2026-09-01", "url": "https://e/old", "source": "test", "fetched_at": "old"}
    current = {"name": "Current Summit", "date_start": "2026-09-02", "url": "https://e/current", "source": "test", "fetched_at": "current"}
    (config.root / "data/candidates-unscored.jsonl").write_text(json.dumps(old) + "\n", encoding="utf-8")
    monkeypatch.setattr("activity_radar.research._score_candidates", lambda _config, batch: batch)

    events, result, _error, pending_count, failures = _score_and_prepare(config, [current], "now", latest_candidates=[current])

    assert [event.name for event in events] == ["Current Summit"]
    assert result == "partial" and pending_count == 1 and failures == []
    assert read_jsonl(config.root / "data/candidates-unscored.jsonl") == [old]


def test_partial_scoring_response_keeps_omitted_candidate_pending(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    candidates = [{"name": "One", "date_start": "2026-09-01", "url": "https://e/1"}, {"name": "Two", "date_start": "2026-09-02", "url": "https://e/2"}]
    monkeypatch.setattr("activity_radar.research._score_candidates", lambda _config, batch: batch[:1])
    scored, pending, failures = _score_candidates_in_batches(config, candidates)
    assert [row["name"] for row in scored] == ["One"]
    assert [row["name"] for row in pending] == ["Two"]
    assert failures == []


def test_each_scoring_batch_writes_usage_log(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    candidates = [{"name": f"Event {index}", "date_start": "2026-09-01", "url": f"https://e/{index}", "source": "test"} for index in range(16)]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, prompt, **kwargs):
            return prompt, Usage(input_tokens=10, output_tokens=2)

    monkeypatch.setattr("activity_radar.research.OpenAIResponsesClient", FakeClient)
    monkeypatch.setattr("activity_radar.research.score_prompt", lambda batch, _scoring: json.dumps(batch))
    scored, pending, failures = _score_candidates_in_batches(config, candidates)
    logs = [row for row in read_jsonl(config.logs_path) if row.get("kind") == "score_batch"]
    assert len(scored) == 16 and pending == [] and failures == []
    assert len(logs) == 2
    assert all(row["usage"] == {"input_tokens": 10, "output_tokens": 2, "cost_usd": None} for row in logs)


def test_pending_cli_scores_and_merges_persisted_candidates(tmp_path, monkeypatch, capsys):
    root = isolated_root(tmp_path)
    candidate = {"name": "Pending Summit", "date_start": "2026-09-10", "date_end": "2026-09-10", "city": "上海", "url": "https://pending.example", "source": "onepilot", "fetched_at": "2026-08-18T00:00:00+00:00"}
    serialized = json.dumps(candidate, ensure_ascii=False) + "\n"
    (root / "data/candidates-latest.jsonl").write_text(serialized, encoding="utf-8")
    (root / "data/candidates-unscored.jsonl").write_text(serialized, encoding="utf-8")

    def fake_score(_config, batch):
        return [{**row, "acquisition_score": 8, "ecosystem_score": 7, "audience_side": "demand", "scale_hint": "unknown", "format": "open", "reason": "需求侧明确。适合业务拓展。"} for row in batch]

    monkeypatch.setattr("activity_radar.research._score_candidates", fake_score)
    assert main(["--root", str(root), "score", "--pending"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["score"]["candidate_count"] == 1
    assert read_jsonl(root / "data/candidates-unscored.jsonl") == []
    assert read_jsonl(root / "data/events.jsonl")[0]["name"] == "Pending Summit"


def test_fixture_run_persists_latest_candidates_with_source_and_fetch_time(tmp_path, capsys):
    root = isolated_root(tmp_path)
    assert main(["--root", str(root), "run", "--fixture", str(root / "fixtures/sample_candidates.json"), "--as-of", "2027-08-18"]) == 0
    capsys.readouterr()
    rows = read_jsonl(root / "data/candidates-latest.jsonl")
    assert len(rows) == 16
    assert all(row.get("source") and row.get("fetched_at") for row in rows)


def test_llm_series_hint_does_not_become_single_event_series():
    event = prepare_event({"name": "Annual Summit", "date_start": "2026-09-01", "date_end": "2026-09-01", "city": "上海", "url": "https://example.com", "is_series": True, "acquisition_score": 7, "ecosystem_score": 7}, "test", "now", {})
    assert event.is_series is False
    assert event.occurrences == []


def test_expected_seed_merges_with_confirmed_same_month_event():
    seed = Event(id=make_id("2026 外滩大会", "2026-09-01"), name="2026 外滩大会", date_start="2026-09-01", date_end="2026-09-01", date_precision="month", city="上海", url="https://seed.example", status="expected", source="calendar-seed", tier="A", acquisition_score=8, ecosystem_score=8, reason="seed")
    exact = Event(id=make_id("Inclusion·外滩大会", "2026-09-09"), name="Inclusion·外滩大会", date_start="2026-09-09", date_end="2026-09-12", city="上海", url="https://exact.example", status="active", source="annual-ai-conferences", tier="A", acquisition_score=8, ecosystem_score=8, reason="exact")
    merged, stats = merge_events([], [seed, exact], {"上海"}, {})
    assert len(merged) == 1
    assert merged[0].date_start == "2026-09-09"
    assert merged[0].status == "active"
    assert merged[0].metadata["seed_id"] == seed.id
    assert stats["new"] == 1


def test_existing_seed_duplicate_collapses_without_new_candidates():
    seed = Event(id=make_id("2026 外滩大会", "2026-09-01"), name="2026 外滩大会", date_start="2026-09-01", date_end="2026-09-01", date_precision="month", city="上海", url="https://seed.example", status="expected", source="calendar-seed", tier="A", acquisition_score=8, ecosystem_score=8, reason="seed")
    exact = Event(id=make_id("Inclusion·外滩大会", "2026-09-09"), name="Inclusion·外滩大会", date_start="2026-09-09", date_end="2026-09-12", city="上海", url="https://exact.example", status="active", source="annual-ai-conferences", tier="A", acquisition_score=8, ecosystem_score=8, reason="exact")
    merged, _ = merge_events([seed, exact], [], {"上海"}, {})
    assert len(merged) == 1
    assert merged[0].metadata["seed_id"] == seed.id


def test_existing_rule_series_survives_but_single_false_series_is_cleared():
    real_series = Event(id="real", name="Weekly Meetup", date_start="2026-09-01", date_end="2026-09-08", city="上海", url="https://real.example", tier="B", acquisition_score=6, ecosystem_score=6, reason="real", is_series=True, occurrences=["2026-09-01", "2026-09-08"])
    false_series = Event(id="false", name="Annual Summit", date_start="2026-10-01", date_end="2026-10-01", city="上海", url="https://false.example", tier="A", acquisition_score=8, ecosystem_score=8, reason="false", is_series=True)
    merged, _ = merge_events([real_series, false_series], [], {"上海"}, {})
    by_id = {event.id: event for event in merged}
    assert by_id["real"].is_series is True
    assert by_id["false"].is_series is False


def test_push_expected_month_health_and_side_event_filters(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    (root / "data/source-health.json").write_text(json.dumps({
        "new-source": {"first_scanned": "2026-08-10T00:00:00+00:00", "last_result": "empty"},
        "stale-source": {"first_scanned": "2026-08-01T00:00:00+00:00", "last_result": "empty"},
    }), encoding="utf-8")
    expected = Event(id="expected", name="Expected Summit", date_start="2026-09-01", date_end="2026-09-01", date_precision="month", city="上海", status="expected", tier="A", acquisition_score=8, ecosystem_score=8, reason="reason", url="https://expected.example")
    one_day_b = Event(id="b", name="One Day B", date_start="2026-08-20", date_end="2026-08-20", city="上海", status="active", tier="B", acquisition_score=6, ecosystem_score=6, reason="reason", url="https://b.example", side_event_opportunity=True)
    message = build_push_for_config([expected, one_day_b], config, today=date(2026, 8, 18))
    assert "9月（日期待官宣）" in message
    assert "🎪 side event 机会" not in message
    assert "新源观察中：2 个" in message
    assert "stale-source" not in message


def test_p1_full_push_limits_detailed_new_events_and_compacts_schedule(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    events = [
        Event(
            id=f"a-{index}",
            name=f"New Event {index}",
            date_start=f"2026-08-{20 + index % 5:02d}",
            date_end=f"2026-08-{20 + index % 5:02d}",
            city="上海",
            event_type="峰会",
            tier="A",
            acquisition_score=8,
            ecosystem_score=8,
            reason="理由一。理由二。理由三。",
            url=f"https://example.com/a/{index}",
            first_seen="2026-08-18T12:00:00+00:00",
        )
        for index in range(14)
    ]
    events.extend([
        Event(id=f"c-{index}", name=f"C Event {index}", date_start="2026-08-25", city="上海", tier="C", acquisition_score=5, ecosystem_score=5, reason="C。", url=f"https://example.com/c/{index}", first_seen="2026-08-18T12:00:00+00:00")
        for index in range(2)
    ])
    message = build_push_for_config(events, config, today=date(2026, 8, 19), mode="full")
    a_section = message.split("⭐ 必看（A 级）\n\n", 1)[1].split("\n\n📅 值得排期", 1)[0]
    b_section = message.split("📅 值得排期（B 级·未来 4 周）\n\n", 1)[1].split("\n\n🌐", 1)[0]
    assert a_section.count("理由一。") == 14
    assert "理由二" not in a_section
    assert "New Event 13" in a_section
    assert "C Event" not in message
    assert b_section == "暂无"


def test_p2_side_event_only_uses_qualified_tier_a_and_caps_related_items(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    qualified = Event(id="qualified", name="Qualified A Summit", date_start="2026-09-01", date_end="2026-09-03", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="A。B。", url="https://example.com/q", side_event_opportunity=True)
    one_day = Event(id="one-day", name="One Day A", date_start="2026-09-01", date_end="2026-09-01", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="A。B。", url="https://example.com/o", side_event_opportunity=True)
    tier_b = Event(id="tier-b", name="Tier B Conference", date_start="2026-09-01", date_end="2026-09-03", city="上海", tier="B", acquisition_score=7, ecosystem_score=7, reason="A。B。", url="https://example.com/b", side_event_opportunity=True)
    related = [Event(id=f"r-{index}", name=f"Related {index}", date_start="2026-09-02", city="上海", tier="B", acquisition_score=6, ecosystem_score=6, reason="A。B。", url=f"https://example.com/r/{index}", related_to="qualified") for index in range(7)]
    message = build_push_for_config([qualified, one_day, tier_b, *related], config, today=date(2026, 8, 19), mode="full")
    side = message.split("🎪 side event 机会\n\n", 1)[1].split("\n\n🌐", 1)[0]
    assert "Qualified A Summit" in side
    assert "One Day A" not in side and "Tier B Conference" not in side
    assert "Related 4" in side and "Related 5" not in side
    assert "🔗 https://example.com/q" in side
    assert "🔗 https://example.com/r/4" in side
    assert "等 2 个周边局见时间轴" in side


def test_p3_source_health_uses_28_day_dates_and_deduplicates_errors(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    (root / "data/source-health.json").write_text(json.dumps({
        "old-empty": {"first_scanned": "2026-07-01T00:00:00+00:00", "last_hit": None, "last_result": "empty"},
        "recent-hit": {"first_scanned": "2026-07-01T00:00:00+00:00", "last_hit": "2026-08-10T00:00:00+00:00", "last_result": "empty"},
        "new-empty": {"first_scanned": "2026-08-10T00:00:00+00:00", "last_hit": None, "last_result": "empty"},
        "broken": {"first_scanned": "2026-07-01T00:00:00+00:00", "last_hit": None, "last_result": "error"},
        "timed-out": {"first_scanned": "2026-08-10T00:00:00+00:00", "last_hit": None, "last_result": "timeout"},
    }), encoding="utf-8")
    message = build_push_for_config([], config, today=date(2026, 8, 19), mode="full")
    health = message.split("🩺 源健康：", 1)[1]
    assert "连续 4 周无 hit：old-empty" in health
    assert "recent-hit" not in health
    assert "新源观察中：1 个" in health
    assert health.count("broken") == 1
    assert "unavailable/异常：broken" in health
    assert "timed-out" not in health


def test_p4_hermes_send_splits_at_1800_chars_and_waits_between_chunks(tmp_path, monkeypatch):
    sent: list[str] = []
    waits: list[int] = []
    monkeypatch.setattr("activity_radar.push.shutil.which", lambda _name: "/fake/hermes")

    def fake_run(*_args, **kwargs):
        sent.append(kwargs["input"])
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("activity_radar.push.subprocess.run", fake_run)
    message = "\n\n".join(f"段落 {index}\n" + "x" * 900 for index in range(5))
    assert all(len(chunk) <= 1800 for chunk in split_message(message))
    result = send_via_hermes(message, "weixin", dry_run=False, log_path=tmp_path / "logs/push.jsonl", sleep_fn=waits.append)
    assert sent == split_message(message)
    assert all(chunk.startswith(f"（{index}/{len(sent)}）") for index, chunk in enumerate(sent, 1))
    assert waits == [45] * (len(sent) - 1)
    assert result["chunks"] == len(sent)


def test_p4_hermes_stops_on_failed_chunk_and_logs_its_number(tmp_path, monkeypatch):
    calls = 0
    monkeypatch.setattr("activity_radar.push.shutil.which", lambda _name: "/fake/hermes")

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        code = 1 if calls >= 2 else 0
        return type("Result", (), {"returncode": code, "stdout": "", "stderr": "cooldown"})()

    monkeypatch.setattr("activity_radar.push.subprocess.run", fake_run)
    log = tmp_path / "logs/push.jsonl"
    with pytest.raises(RuntimeError, match="chunk 2/"):
        send_via_hermes("A" * 1700 + "\n\n" + "B" * 1700 + "\n\n" + "C" * 1700, "weixin", dry_run=False, log_path=log, sleep_fn=lambda _seconds: None)
    # Chunk 1 succeeds; chunk 2 is retried twice before the final failure.
    assert calls == 4
    rows = read_jsonl(log)
    assert [r["status"] for r in rows] == ["sent", "retrying", "retrying", "failed"]
    assert rows[0]["chunk"] == 1
    assert all(r["chunk"] == 2 for r in rows[1:])
    assert len({r["message_id"] for r in rows}) == 1


def test_p5_new_discoveries_use_previous_full_or_seven_day_fallback(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    old = Event(id="old", name="Old Event", date_start="2026-08-25", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="A。B。", url="https://example.com/old", first_seen="2026-08-11T12:00:00+00:00")
    recent = Event(id="recent", name="Recent Event", date_start="2026-08-25", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="A。B。", url="https://example.com/recent", first_seen="2026-08-18T12:00:00+00:00")
    no_history = build_push_for_config([old, recent], config, today=date(2026, 8, 19), mode="delta")
    first_section = no_history.split("🆕 新增\n\n", 1)[1].split("\n\n🌐", 1)[0]
    assert "Old Event" not in first_section and "Recent Event" in first_section
    history = root / "data/push-history"
    history.mkdir()
    (history / "20260818T130000Z-full.txt").write_text("sample", encoding="utf-8")
    after_history = Event(id="after", name="After Full", date_start="2026-08-25", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="A。B。", url="https://example.com/after", first_seen="2026-08-18T14:00:00+00:00")
    with_history = build_push_for_config([recent, after_history], config, today=date(2026, 8, 19), mode="delta")
    first_section = with_history.split("🆕 新增\n\n", 1)[1].split("\n\n🌐", 1)[0]
    assert "Recent Event" not in first_section and "After Full" in first_section


def test_p6_push_ends_with_timeline_link(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    message = build_push_for_config([], config, today=date(2026, 8, 19), mode="delta")
    assert message.endswith("🌐 完整时间轴 https://potato-uu.github.io/activity-radar/")


def test_wechat_full_format_snapshot_has_required_lines_in_order(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    events = [
        Event(id="a", name="AI 出海大会", date_start="2026-08-20", city="上海", event_type="峰会", format="open", tier="A", acquisition_score=9, ecosystem_score=6, reason="出海广告主与操盘手密度高。第二句不应出现。", url="https://example.com/a"),
        Event(id="b", name="品牌增长沙龙", date_start="2026-08-21", city="上海", event_type="沙龙·meetup", format="closed_door", tier="B", acquisition_score=7, ecosystem_score=5, reason="值得排期。", url="https://example.com/b"),
    ]
    message = build_push_for_config(events, config, today=date(2026, 8, 19), mode="full")
    expected_lines = [
        "📡 BD 活动雷达｜8/19 全量",
        "⭐ 必看（A 级）",
        "① 8/20 周四｜AI 出海大会",
        "获客9 资源6｜峰会·上海·公开",
        "出海广告主与操盘手密度高。",
        "🔗 https://example.com/a",
        "📅 值得排期（B 级·未来 4 周）",
        "· 8/21 周五｜品牌增长沙龙｜获客7",
        "🔗 https://example.com/b",
        "🌐 完整时间轴 https://potato-uu.github.io/activity-radar/",
        "🩺 源健康：",
    ]
    positions = [message.index(line) for line in expected_lines]
    assert positions == sorted(positions)
    assert "\n\n① 8/20" in message
    assert "https://example.com/a\n\n📅" in message


def test_wechat_a_reason_uses_first_sentence_and_is_at_most_40_chars(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    reason = "这是一句超过四十个字的理由用于验证微信推送会在规定上限内截断并且始终以句号结尾不会溢出。这是第二句。"
    event = Event(id="a", name="超长理由大会", date_start="2026-08-20", city="上海", tier="A", acquisition_score=9, ecosystem_score=8, reason=reason, url="https://example.com/a")
    message = build_push_for_config([event], config, today=date(2026, 8, 19), mode="full")
    reason_line = message.split("获客9 资源8｜峰会·上海·公开\n", 1)[1].splitlines()[0]
    assert len(reason_line) <= 40
    assert reason_line.endswith("。")
    assert "这是第二句" not in message


def test_wechat_reason_does_not_split_at_decimal_point(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    event = Event(id="a", name="小数理由大会", date_start="2026-08-20", city="上海", tier="A", acquisition_score=9, ecosystem_score=8, reason="获客 3.5 分，仍值得关注。第二句。", url="https://example.com/a")

    message = build_push_for_config([event], config, today=date(2026, 8, 19), mode="full")

    assert "获客 3.5 分，仍值得关注。" in message


def test_wechat_expected_series_and_review_markers(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    expected = Event(id="expected", name="金投赏", date_start="2026-10-01", date_precision="month", status="expected", city="上海", tier="A", acquisition_score=8, ecosystem_score=7, reason="品牌方密度高。", url="https://example.com/expected", needs_review=True)
    series = Event(id="series", name="每周 AI 沙龙", date_start="2026-08-20", city="上海", tier="B", acquisition_score=7, ecosystem_score=6, reason="操盘手密度高。", url="https://example.com/series", is_series=True, occurrences=["2026-08-20", "2026-08-27"])
    message = build_push_for_config([expected, series], config, today=date(2026, 8, 19), mode="full")
    assert "① 10月（日期待官宣）｜金投赏 ⚠️" in message
    assert "· 8/20 周四（每周，下一场 8/27）｜每周 AI 沙龙｜获客7" in message


def test_wechat_delta_groups_changes_and_keeps_each_link_on_own_line(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    events = [
        Event(id="new", name="新增活动", date_start="2026-08-20", city="上海", tier="B", acquisition_score=7, ecosystem_score=6, reason="新增。", url="https://example.com/new", first_seen="2026-08-19T00:00:00+00:00"),
        Event(id="changed", name="变更活动", date_start="2026-08-21", city="上海", tier="B", acquisition_score=6, ecosystem_score=7, reason="变更。", url="https://example.com/changed", status="changed"),
        Event(id="cancelled", name="取消活动", date_start="2026-08-22", city="上海", tier="B", acquisition_score=6, ecosystem_score=6, reason="取消。", url="https://example.com/cancelled", status="cancelled"),
    ]
    message = build_push_for_config(events, config, today=date(2026, 8, 19), mode="delta")
    headings = ["🆕 新增", "✏️ 变更", "❌ 取消"]
    assert [message.index(heading) for heading in headings] == sorted(message.index(heading) for heading in headings)
    for url in ("new", "changed", "cancelled"):
        assert f"\n🔗 https://example.com/{url}\n" in message
    assert "🌐 完整时间轴 https://potato-uu.github.io/activity-radar/" in message


def test_wechat_numbering_uses_circled_numbers_through_20_then_falls_back(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    events = [
        Event(id=str(index), name=f"A 级活动 {index}", date_start="2026-08-20", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="值得参加。", url=f"https://example.com/{index}")
        for index in range(1, 22)
    ]
    message = build_push_for_config(events, config, today=date(2026, 8, 19), mode="full")
    assert "\n\n⑪ 8/20 周四｜" in message
    assert "\n\n⑳ 8/20 周四｜" in message
    assert "\n\n21. 8/20 周四｜" in message


def test_wechat_omits_non_http_link_and_delta_no_change_keeps_timeline(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    event = Event(id="a", name="无效链接大会", date_start="2026-08-20", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="值得参加。", url="javascript:alert(1)")
    full = build_push_for_config([event], config, today=date(2026, 8, 19), mode="full")
    delta = build_push_for_config([], config, today=date(2026, 8, 19), mode="delta")
    assert "无效链接大会" in full
    assert "🔗 javascript:" not in full
    assert "本周三无新增，本周无变更，雷达正常，下次周日 18:00" in delta
    assert delta.endswith("🌐 完整时间轴 https://potato-uu.github.io/activity-radar/")


def test_wechat_default_header_date_uses_shanghai_timezone(tmp_path, monkeypatch):
    config = RadarConfig.load(isolated_root(tmp_path))
    shanghai_now = datetime(2026, 8, 20, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("activity_radar.push._shanghai_time", lambda _now=None: shanghai_now)
    message = build_push_for_config([], config, mode="full")
    assert message.startswith("📡 BD 活动雷达｜8/20 全量")


def test_wechat_delta_only_includes_changes_after_last_full(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    history = root / "data/push-history"
    history.mkdir()
    (history / "20260819T130000Z-full.txt").write_text("sample", encoding="utf-8")
    before = Event(id="before", name="历史变更活动", date_start="2026-08-21", city="上海", tier="B", acquisition_score=6, ecosystem_score=6, reason="旧变更。", url="https://example.com/before", status="changed", last_verified="2026-08-19T12:00:00+00:00")
    after = Event(id="after", name="本次取消活动", date_start="2026-08-22", city="上海", tier="B", acquisition_score=6, ecosystem_score=6, reason="新取消。", url="https://example.com/after", status="cancelled", last_verified="2026-08-19T14:00:00+00:00")
    message = build_push_for_config([before, after], config, today=date(2026, 8, 19), mode="delta")
    assert "历史变更活动" not in message
    assert "❌ 取消" in message and "本次取消活动" in message


def test_auto_mode_uses_shanghai_hours_and_is_idempotent(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    sunday = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)  # 18:00 Shanghai
    wednesday = datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc)  # 10:00 Shanghai
    other = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    assert auto_mode(sunday) == "full"
    assert auto_mode(wednesday) == "delta"
    assert auto_mode(other) is None
    record_auto_success(config, "full", sunday)
    assert (root / "data/push-history/2026-08-23-full.success").exists()
    assert has_successful_auto_run(config, sunday, "full") is True


def test_missing_hermes_path_is_explicit_and_logged(tmp_path, monkeypatch):
    log = tmp_path / "logs/push.jsonl"
    monkeypatch.setattr("activity_radar.push.shutil.which", lambda _name: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="Hermes executable not found"):
        send_via_hermes("hello", "weixin", dry_run=False, log_path=log)
    assert read_jsonl(log)[0]["status"] == "failed"


def test_workflow_and_rendered_fixtures_are_wired():
    workflow = (ROOT / ".github/workflows/radar.yml").read_text(encoding="utf-8")
    assert "timeout-minutes: 90" in workflow
    assert "playwright install --with-deps chromium" in workflow
    sources = {row["id"]: row for row in RadarConfig.load(ROOT).sources}
    for source_id in ("huodongxing", "luma-shanghai-ai", "mosu-space", "10times", "meetup-shanghai-ai"):
        source = sources[source_id]
        assert source["adapter"] == "rendered"
        fixture = ROOT / "fixtures/rendered" / f"{source_id}.html"
        rows = extract_html_candidates(fixture.read_text(encoding="utf-8"), {**source, "url": "https://example.test/events"}, "now")
        assert len(rows) == 1


def test_readme_documents_pending_and_auto_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "CODEX_BASE_URL" in readme and "不含密钥" in readme
    assert "radar score --pending" in readme
    assert "radar push --auto" in readme


def test_prefilter_drops_navigation_missing_dates_and_nonofficial_other_cities(tmp_path):
    scoring = RadarConfig.load(isolated_root(tmp_path)).scoring
    rows = [
        {"name": "Skip to main content", "date_start": "", "city": "上海", "source": "google-developers-cn"},
        {"name": "报名中", "date_start": "2027-08-20", "city": "上海", "source": "cifnews-ccee"},
        {"name": "eMAG2027中国卖家峰会—深圳", "date_start": "2027-08-20", "city": "深圳", "source": "cifnews-ccee"},
        {"name": "Google 官方深圳开发者大会", "date_start": "2027-09-01", "city": "深圳", "source": "google-developers-cn"},
        {"name": "Shanghai AI Growth Summit", "date_start": "2027-09-02", "city": "上海", "source": "onepilot"},
        {"name": "海外品牌峰会", "date_start": "2027-09-03", "city": "海外", "source": "calendar-seed"},
    ]
    kept, counts = prefilter_candidates(rows, scoring, today=date(2027, 8, 19))
    assert [row["name"] for row in kept] == ["Google 官方深圳开发者大会", "Shanghai AI Growth Summit"]
    assert counts == {"invalid": 2, "past": 0, "out_of_scope": 1, "overseas": 1}


def test_short_title_filter_counts_all_letters_digits_and_has_named_exceptions(tmp_path):
    scoring = RadarConfig.load(isolated_root(tmp_path)).scoring
    for name in ("2026 金投赏", "WAIC", "金投赏"):
        assert is_valid_candidate({"name": name, "date_start": "2026-10-01"}, scoring)[0] is True
    assert is_valid_candidate({"name": "AI", "date_start": "2026-10-01"}, scoring) == (False, "title_too_short")


def test_prefilter_counts_explicitly_past_candidates(tmp_path):
    scoring = RadarConfig.load(isolated_root(tmp_path)).scoring
    kept, counts = prefilter_candidates(
        [{"name": "2025 Google DevFest", "date_start": "2025-11-15", "city": "上海", "source": "gdg-east-china"}],
        scoring,
        today=date(2026, 8, 19),
    )
    assert kept == []
    assert counts["past"] == 1


def test_merge_prefers_structured_type_and_highest_scores_and_history():
    seed = Event(
        id=make_id("2026 Google DevFest 上海", "2026-11-07"),
        name="2026 Google DevFest 上海",
        date_start="2026-11-07",
        date_end="2026-11-07",
        city="上海",
        url="https://seed.example",
        source="annual-ai-conferences",
        event_type="开发者大会",
        acquisition_score=6,
        ecosystem_score=8,
        tier="A",
        organizer="Google",
        score_history=[{"timestamp": "seed", "acquisition_score": 6, "ecosystem_score": 8}],
    )
    detailed = Event(
        id=make_id("⚡️ 2026 Google Devfest 谷歌开发者节", "2026-11-07"),
        name="⚡️ 2026 Google Devfest 谷歌开发者节",
        date_start="2026-11-07",
        date_end="2026-11-07",
        city="上海",
        url="https://onepilot.example",
        source="onepilot",
        event_type="沙龙·meetup",
        acquisition_score=7,
        ecosystem_score=7,
        tier="B",
        organizer="GDG Shanghai",
        venue="上海徐汇",
        score_history=[{"timestamp": "onepilot", "acquisition_score": 7, "ecosystem_score": 7}],
    )
    merged, _ = merge_events([], [seed, detailed], {"上海"}, {})
    assert len(merged) == 1
    assert merged[0].event_type == "开发者大会"
    assert merged[0].acquisition_score == 7
    assert merged[0].ecosystem_score == 8
    assert len(merged[0].score_history) >= 2
    assert {entry["acquisition_score"] for entry in merged[0].score_history} >= {6, 7}


def test_data_quality_source_selectors_ignore_page_navigation_and_articles():
    sources = {row["id"]: row for row in RadarConfig.load(ROOT).sources}
    for source_id in ("google-developers-cn", "aws-china-events"):
        html = (ROOT / "fixtures/data_quality" / f"{source_id}.html").read_text(encoding="utf-8")
        rows = extract_html_candidates(html, sources[source_id], "now")
        assert len(rows) == 1
        assert rows[0].raw_title not in {"Skip to main content", "跳至主要内容"}
    for source_id in ("morketing", "brandstar"):
        html = (ROOT / "fixtures/data_quality" / f"{source_id}-articles.html").read_text(encoding="utf-8")
        assert extract_html_candidates(html, sources[source_id], "now") == []
        assert "/events" in sources[source_id]["url"]
        assert "文章" in sources[source_id]["empty_reason"]


def test_article_only_source_is_recorded_empty_with_specific_reason(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.sources = [source for source in config.sources if source["id"] == "morketing"]
    update_source_health(config, {
        "source_ids": ["morketing"],
        "source_hits": [],
        "source_errors": [],
        "source_candidate_counts": {"morketing": 0},
        "source_empty_reasons": {"morketing": config.sources[0]["empty_reason"]},
    })
    row = read_json(root / "data/source-health.json", {})["morketing"]
    assert row["last_result"] == "empty"
    assert "未使用新闻/文章标题" in row["reason"]


def test_training_and_small_open_salon_regressions_are_at_most_tier_b():
    scoring = RadarConfig.load(ROOT).scoring
    rows = json.loads((ROOT / "fixtures/data_quality/regressions.json").read_text(encoding="utf-8"))["events"][:2]
    events = [prepare_event(row, row["source"], "now", scoring) for row in rows]
    assert all(event.tier in {"B", "C", "D"} for event in events)
    assert events[0].acquisition_score <= 3
    assert events[1].acquisition_score <= 7 and events[1].ecosystem_score <= 7


def test_devfest_cross_source_token_merge_keeps_richer_record_and_provenance():
    scoring = RadarConfig.load(ROOT).scoring
    rows = json.loads((ROOT / "fixtures/data_quality/regressions.json").read_text(encoding="utf-8"))["events"][2:]
    events = [prepare_event(row, row["source"], "now", scoring) for row in rows]
    merged, _ = merge_events([], events, {"上海"}, scoring)
    assert len(merged) == 1
    assert merged[0].organizer == "GDG Shanghai"
    assert merged[0].source == "onepilot"
    assert set(merged[0].metadata["merged_from"]) == {events[0].id, events[1].id}


def test_push_and_timeline_drop_invalid_entries_and_show_type_city_format(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    good = Event(id="good", name="AI 出海闭门沙龙", date_start="2026-08-22", date_end="2026-08-22", city="上海", event_type="沙龙·meetup", format="closed_door", tier="A", acquisition_score=8, ecosystem_score=7, reason="需求侧负责人在场。适合业务拓展。", url="https://good.example", first_seen="2026-08-19")
    bad = Event(id="bad", name="Skip to main content", date_start="", date_end="", city="上海", tier="A", acquisition_score=9, ecosystem_score=9, reason="bad", url="https://bad.example", first_seen="2026-08-19")
    message = build_push_for_config([good, bad], config, today=date(2026, 8, 19))
    assert "沙龙·上海·闭门" in message
    assert "Skip to main content" not in message
    output = root / "site/index.html"
    render_timeline([good, bad], output, "now", config.scoring)
    page = output.read_text(encoding="utf-8")
    assert "AI 出海闭门沙龙" in page
    assert "Skip to main content" not in page


def test_send_via_hermes_retries_rate_limited_chunk(tmp_path, monkeypatch):
    """A chunk that fails once must be retried after a cooldown, then succeed."""
    from types import SimpleNamespace
    from activity_radar import push as push_mod

    calls = {"n": 0}
    sleeps: list[int] = []

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(returncode=1, stdout='{"error": "iLink sendmessage rate limited"}', stderr="")
        return SimpleNamespace(returncode=0, stdout='{"success": true}', stderr="")

    monkeypatch.setattr(push_mod.shutil, "which", lambda name: "/usr/bin/true")
    monkeypatch.setattr(push_mod.subprocess, "run", fake_run)
    log_path = tmp_path / "push.jsonl"
    result = push_mod.send_via_hermes("hello", "weixin", dry_run=False, log_path=log_path, sleep_fn=sleeps.append)
    assert result["status"] == "sent"
    assert calls["n"] == 2
    assert 120 in sleeps
    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert any(r.get("status") == "retrying" and "rate limited" in r.get("error", "") for r in rows)


def test_send_via_hermes_does_not_retry_permanent_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from activity_radar import push as push_mod

    calls = {"n": 0}
    sleeps: list[int] = []

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="authentication failed")

    monkeypatch.setattr(push_mod.shutil, "which", lambda name: "/usr/bin/true")
    monkeypatch.setattr(push_mod.subprocess, "run", fake_run)
    log_path = tmp_path / "push.jsonl"

    with pytest.raises(RuntimeError, match="after 1 attempts"):
        push_mod.send_via_hermes("hello", "weixin", dry_run=False, log_path=log_path, sleep_fn=sleeps.append)

    assert calls["n"] == 1
    assert sleeps == []
    rows = read_jsonl(log_path)
    assert rows[-1]["status"] == "failed"
    assert rows[-1]["attempt"] == 1


def test_auto_mode_catches_up_later_same_day():
    """A missed exact hour still sends later the same Shanghai day."""
    late_sunday = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)  # 23:00 Shanghai Sunday
    late_wednesday = datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc)  # 15:00 Shanghai Wednesday
    before_window = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)  # 17:00 Shanghai Sunday
    assert auto_mode(late_sunday) == "full"
    assert auto_mode(late_wednesday) == "delta"
    assert auto_mode(before_window) is None


def test_r1_runtime_clone_recovers_artifacts_and_warns_when_pull_fails(tmp_path, monkeypatch, capsys):
    root = isolated_root(tmp_path)
    monkeypatch.setenv("RADAR_GIT_PULL_FAILED", "1")

    assert main(["--root", str(root), "push", "--mode", "full"]) == 0
    capsys.readouterr()

    message = (root / "data/push-latest.txt").read_text(encoding="utf-8")
    rows = read_jsonl(root / "logs/push.jsonl")
    script = (ROOT / "scripts/push_local.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/radar.yml").read_text(encoding="utf-8")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert message.endswith("⚠️ 数据未更新（git pull 失败）")
    assert any(row.get("kind") == "pull_failed" for row in rows)
    assert script.index("git checkout -- logs data site") < script.index("git pull --ff-only")
    assert "|| git checkout -- data site" in script
    assert 'git add data site' in workflow
    assert 'git add data logs site' not in workflow
    assert "logs/*.jsonl" in ignore


def test_r2_clean_names_migration_is_idempotent_and_preserves_scores_and_status(tmp_path, capsys):
    root = isolated_root(tmp_path)
    original = Event(
        id="dirty-amz",
        name="2026拉美跨境电商赋能大会·杭州站 2026-08-27 浙江省杭州市",
        date_start="2026-08-27",
        date_end="2026-08-27",
        city="上海",
        source="amz123",
        url="https://www.amz123.com/hd/example",
        acquisition_score=6,
        ecosystem_score=4,
        tier="B",
        status="changed",
        score_history=[{"timestamp": "2026-08-19T00:00:00+00:00", "acquisition_score": 6, "ecosystem_score": 4}],
    )
    (root / "data/events.jsonl").write_text(json.dumps(original.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")

    assert main(["--root", str(root), "migrate", "--clean-names"]) == 0
    first = json.loads(capsys.readouterr().out)
    migrated = read_jsonl(root / "data/events.jsonl")[0]
    assert first["changed_count"] == 1
    assert migrated["name"] == "2026拉美跨境电商赋能大会·杭州站"
    assert migrated["city"] == "杭州"
    assert migrated["acquisition_score"] == 6
    assert migrated["ecosystem_score"] == 4
    assert migrated["tier"] == "B"
    assert migrated["status"] == "changed"
    assert migrated["score_history"] == original.score_history

    assert main(["--root", str(root), "migrate", "--clean-names"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["changed_count"] == 0
    logs = read_jsonl(root / "logs/migrate.jsonl")
    assert [row["changed_count"] for row in logs] == [1, 0]


def test_r4_private_reimbursements_directory_is_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "reimbursements/" in ignore


def test_s1_fresh_research_allows_auto_push(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    (root / "data/research-meta.json").write_text(
        json.dumps({"completed_at": "2026-08-23T09:30:00Z", "git_sha": "abc", "event_count": 2, "mode": "full"}),
        encoding="utf-8",
    )

    status, warning = push_mod.research_freshness(config, datetime(2026, 8, 23, 10, 30, tzinfo=timezone.utc), "full")

    assert status == "fresh"
    assert warning is None


def test_s1_stale_research_waits_before_cutoff_and_logs_skip(tmp_path, monkeypatch, capsys):
    root = isolated_root(tmp_path)
    (root / "data/research-meta.json").write_text(
        json.dumps({"completed_at": "2026-08-22T08:00:00Z", "git_sha": "abc", "event_count": 2, "mode": "full"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("activity_radar.cli._utc_now", lambda: datetime(2026, 8, 23, 10, 30, tzinfo=timezone.utc))

    assert main(["--root", str(root), "push", "--auto"]) == 0

    assert capsys.readouterr().out.strip() == "skip"
    row = read_jsonl(root / "logs/push.jsonl")[-1]
    assert row["kind"] == "auto_skip"
    assert row["reason"] == "waiting_fresh_data"


def test_s1_stale_research_sends_after_cutoff_with_dated_warning(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    (root / "data/research-meta.json").write_text(
        json.dumps({"completed_at": "2026-08-22T08:00:00Z", "git_sha": "abc", "event_count": 2, "mode": "full"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("activity_radar.cli._utc_now", lambda: datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc))

    assert main(["--root", str(root), "push", "--auto"]) == 0

    message = (root / "data/push-latest.txt").read_text(encoding="utf-8")
    assert message.endswith("⚠️ 数据为 8/22 研究结果（今日研究未完成）")


def test_s1_missing_research_meta_never_invents_a_research_date(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))

    status, warning = push_mod.research_freshness(config, datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc), "full")

    assert status == "stale"
    assert warning == "⚠️ 未找到研究结果日期（今日研究未完成）"


def test_s1_live_run_writes_research_meta_with_read_only_git_sha(tmp_path, monkeypatch, capsys):
    root = isolated_root(tmp_path)
    stats = {"source_ids": [], "source_hits": [], "source_errors": [], "source_error_details": {}}
    monkeypatch.setattr("activity_radar.cli.discover_and_score", lambda *args, **kwargs: ([], stats))
    monkeypatch.setattr("activity_radar.cli.render_timeline", lambda *args, **kwargs: None)
    monkeypatch.setattr("activity_radar.cli.build_push_for_config", lambda *args, **kwargs: "sample")
    monkeypatch.setattr("activity_radar.cli.write_push_artifacts", lambda *args, **kwargs: {})
    monkeypatch.setattr("activity_radar.cli.send_via_hermes", lambda *args, **kwargs: {"status": "dry_run"})
    monkeypatch.setattr("activity_radar.cli._read_git_sha", lambda _root: "abc123")
    monkeypatch.setattr("activity_radar.cli._utc_now", lambda: datetime(2026, 8, 23, 9, 45, tzinfo=timezone.utc))

    assert main(["--root", str(root), "run", "--live", "--push-mode", "delta"]) == 0
    capsys.readouterr()

    assert read_json(root / "data/research-meta.json", {}) == {
        "completed_at": "2026-08-23T09:45:00+00:00",
        "git_sha": "abc123",
        "event_count": 0,
        "mode": "delta",
    }


def test_s2_failed_send_persists_outbox_with_sent_message_id(tmp_path, monkeypatch):
    from types import SimpleNamespace

    message = "A" * 1700 + "\n\n" + "B" * 1700 + "\n\n" + "C" * 1700
    outbox = tmp_path / "data/push-history/2026-08-23-full.outbox.json"
    marker = tmp_path / "data/push-history/2026-08-23-full.success"
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0 if calls == 1 else 1, stdout="ok" if calls == 1 else "", stderr="authentication failed" if calls > 1 else "")

    monkeypatch.setattr(push_mod.shutil, "which", lambda _name: "/fake/hermes")
    monkeypatch.setattr(push_mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="chunk 2/"):
        send_via_hermes(
            message,
            "weixin",
            dry_run=False,
            outbox_path=outbox,
            success_marker=marker,
            sleep_fn=lambda _seconds: None,
        )

    saved = read_json(outbox, {})
    assert saved["chunks"] == split_message(message)
    assert list(saved["sent"]) == ["1"]
    assert saved["sent"]["1"]
    assert saved["created_at"]
    assert marker.exists() is False


def test_s2_resume_prefers_saved_chunks_then_cleans_outbox_and_marks_success(tmp_path, monkeypatch):
    from types import SimpleNamespace

    outbox = tmp_path / "data/push-history/2026-08-23-full.outbox.json"
    marker = tmp_path / "data/push-history/2026-08-23-full.success"
    chunks = ["（1/3）\nsaved one", "（2/3）\nsaved two", "（3/3）\nsaved three"]
    outbox.parent.mkdir(parents=True)
    outbox.write_text(json.dumps({"chunks": chunks, "sent": {"1": "saved-message-id"}, "created_at": "2026-08-23T10:00:00+00:00"}), encoding="utf-8")
    sent: list[str] = []

    def fake_run(*_args, **kwargs):
        sent.append(kwargs["input"])
        return SimpleNamespace(returncode=0, stdout='{"success": true}', stderr="")

    monkeypatch.setattr(push_mod.shutil, "which", lambda _name: "/fake/hermes")
    monkeypatch.setattr(push_mod.subprocess, "run", fake_run)

    result = send_via_hermes(
        "conflicting newly generated message",
        "weixin",
        dry_run=False,
        outbox_path=outbox,
        success_marker=marker,
        sleep_fn=lambda _seconds: None,
    )

    assert sent == chunks[1:]
    assert result["message_id"] == "saved-message-id"
    assert marker.exists()
    assert outbox.exists() is False


def test_s2_auto_push_resumes_outbox_before_freshness_gate(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    root = isolated_root(tmp_path)
    outbox = root / "data/push-history/2026-08-23-full.outbox.json"
    outbox.parent.mkdir(parents=True)
    saved_chunk = "（1/1）\nold stable content"
    outbox.write_text(json.dumps({"chunks": [saved_chunk], "sent": {}, "created_at": "2026-08-23T10:00:00+00:00"}), encoding="utf-8")
    sent: list[str] = []

    monkeypatch.setattr("activity_radar.cli._utc_now", lambda: datetime(2026, 8, 23, 10, 30, tzinfo=timezone.utc))
    monkeypatch.setattr(push_mod.shutil, "which", lambda _name: "/fake/hermes")
    monkeypatch.setattr(push_mod.subprocess, "run", lambda *_args, **kwargs: sent.append(kwargs["input"]) or SimpleNamespace(returncode=0, stdout="ok", stderr=""))

    assert main(["--root", str(root), "push", "--auto", "--send"]) == 0
    capsys.readouterr()

    assert sent == [saved_chunk]
    assert outbox.exists() is False
    assert (root / "data/push-history/2026-08-23-full.success").exists()


def test_s2_outbox_files_are_ignored():
    assert "data/push-history/*.outbox.json" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_s3_backfill_audit_applies_supply_training_and_salon_caps(tmp_path, capsys):
    root = isolated_root(tmp_path)
    events = [
        Event(id="supply", name="供应侧大会", date_start="2026-09-01", city="上海", audience_side="supply", event_type="峰会", format="open", acquisition_score=9, ecosystem_score=5, tier="A"),
        Event(id="training", name="AI 培训", date_start="2026-09-02", city="上海", audience_side="demand", event_type="training", format="open", acquisition_score=9, ecosystem_score=6, tier="A"),
        Event(id="salon", name="开放沙龙", date_start="2026-09-03", city="上海", audience_side="demand", event_type="沙龙·meetup", format="open", scale_hint="unknown", acquisition_score=9, ecosystem_score=9, tier="A"),
    ]
    (root / "data/events.jsonl").write_text("".join(json.dumps(event.to_dict(), ensure_ascii=False) + "\n" for event in events), encoding="utf-8")

    assert main(["--root", str(root), "migrate", "--backfill-audit"]) == 0
    result = json.loads(capsys.readouterr().out)
    migrated = {row["id"]: row for row in read_jsonl(root / "data/events.jsonl")}

    assert result["changed_count"] == 3
    assert result["score_changed_count"] == 3
    assert (migrated["supply"]["acquisition_score"], migrated["supply"]["tier"]) == (4, "C")
    assert (migrated["training"]["acquisition_score"], migrated["training"]["tier"]) == (3, "B")
    assert (migrated["salon"]["acquisition_score"], migrated["salon"]["ecosystem_score"], migrated["salon"]["tier"]) == (7, 7, "B")
    assert migrated["supply"]["metadata"]["score_audit"]["caps_applied"] == ["supply_acquisition_cap"]
    assert migrated["training"]["metadata"]["score_audit"]["caps_applied"] == ["pure_training_acquisition_cap"]
    assert migrated["salon"]["metadata"]["score_audit"]["caps_applied"] == ["small_open_salon_cap"]


def test_s3_backfill_audit_keeps_compliant_scores_unchanged(tmp_path, capsys):
    root = isolated_root(tmp_path)
    event = Event(id="compliant", name="供应侧小会", date_start="2026-09-01", city="上海", audience_side="supply", acquisition_score=4, ecosystem_score=5, tier="C")
    (root / "data/events.jsonl").write_text(json.dumps(event.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")

    assert main(["--root", str(root), "migrate", "--backfill-audit"]) == 0
    result = json.loads(capsys.readouterr().out)
    migrated = read_jsonl(root / "data/events.jsonl")[0]

    assert result["changed_count"] == 1
    assert result["score_changed_count"] == 0
    assert (migrated["acquisition_score"], migrated["ecosystem_score"], migrated["tier"]) == (4, 5, "C")
    assert migrated["metadata"]["score_audit"] == {
        "backfilled": True,
        "caps_applied": [],
        "final": {"acquisition_score": 4, "ecosystem_score": 5},
    }


def test_s3_backfill_audit_is_idempotent_on_second_run(tmp_path, capsys):
    root = isolated_root(tmp_path)
    event = Event(id="supply", name="供应侧大会", date_start="2026-09-01", city="上海", audience_side="supply", acquisition_score=9, ecosystem_score=5, tier="A")
    (root / "data/events.jsonl").write_text(json.dumps(event.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")

    assert main(["--root", str(root), "migrate", "--backfill-audit"]) == 0
    first = json.loads(capsys.readouterr().out)
    after_first = (root / "data/events.jsonl").read_bytes()
    assert main(["--root", str(root), "migrate", "--backfill-audit"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["changed_count"] == 1
    assert second["changed_count"] == 0
    assert (root / "data/events.jsonl").read_bytes() == after_first
    assert [row["changed_count"] for row in read_jsonl(root / "logs/migrate.jsonl")] == [1, 0]


def test_s3_backfill_audit_never_replays_subtractive_corrections(tmp_path, capsys):
    root = isolated_root(tmp_path)
    event = Event(
        id="subtractive",
        name="杭州小型邀约峰会",
        date_start="2026-09-01",
        city="杭州",
        audience_side="demand",
        event_type="峰会",
        format="invite_only",
        scale_hint="small",
        acquisition_score=8,
        ecosystem_score=8,
        tier="A",
    )
    (root / "data/events.jsonl").write_text(json.dumps(event.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")

    assert main(["--root", str(root), "migrate", "--backfill-audit"]) == 0
    capsys.readouterr()
    migrated = read_jsonl(root / "data/events.jsonl")[0]

    assert (migrated["acquisition_score"], migrated["ecosystem_score"], migrated["tier"]) == (8, 8, "A")
    assert migrated["metadata"]["score_audit"]["caps_applied"] == []


def _s4_stub_runtime(tmp_path: Path, *, pip_exit: int = 0) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path / "runtime"
    (root / ".venv/bin").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='stub'\n", encoding="utf-8")
    log = tmp_path / "stub-calls.log"
    python_stub = root / ".venv/bin/python"
    python_stub.write_text(
        "#!/bin/zsh\n"
        "print -r -- \"$*\" >> \"$RADAR_TEST_LOG\"\n"
        "if [[ \"$*\" == *\"-m pip install -q -e .\"* ]]; then exit \"$RADAR_STUB_PIP_EXIT\"; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    python_stub.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_stub = bin_dir / "git"
    git_stub.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
    git_stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RADAR_ROOT_OVERRIDE": str(root),
        "RADAR_TEST_LOG": str(log),
        "RADAR_STUB_PIP_EXIT": str(pip_exit),
    }
    return root, log, env


def test_s4_push_script_updates_dependencies_only_when_pyproject_hash_changes(tmp_path):
    script = ROOT / "scripts/push_local.sh"
    script_text = script.read_text(encoding="utf-8")
    assert "RADAR_ROOT_OVERRIDE" in script_text
    root, log, env = _s4_stub_runtime(tmp_path)
    marker = root / ".venv/.pyproject.sha256"
    marker.write_text("stale\n", encoding="utf-8")

    first = subprocess.run(["zsh", str(script)], env=env, text=True, capture_output=True, check=False, timeout=10)

    assert first.returncode == 0, first.stderr
    first_calls = log.read_text(encoding="utf-8").splitlines()
    assert "-m pip install -q -e ." in first_calls
    assert "-m activity_radar.cli push --auto --send" in first_calls
    expected_hash = hashlib.sha256((root / "pyproject.toml").read_bytes()).hexdigest()
    assert marker.read_text(encoding="utf-8").strip() == expected_hash

    log.write_text("", encoding="utf-8")
    second = subprocess.run(["zsh", str(script)], env=env, text=True, capture_output=True, check=False, timeout=10)

    assert second.returncode == 0, second.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["-m activity_radar.cli push --auto --send"]


def test_s4_failed_dependency_install_keeps_old_environment_and_pushes(tmp_path):
    script = ROOT / "scripts/push_local.sh"
    assert "RADAR_ROOT_OVERRIDE" in script.read_text(encoding="utf-8")
    root, log, env = _s4_stub_runtime(tmp_path, pip_exit=1)
    marker = root / ".venv/.pyproject.sha256"
    marker.write_text("old-hash\n", encoding="utf-8")

    result = subprocess.run(["zsh", str(script)], env=env, text=True, capture_output=True, check=False, timeout=10)

    assert result.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == "old-hash"
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls == ["-m pip install -q -e .", "-m activity_radar.cli push --auto --send"]
    assert "dependency update failed" in result.stderr


def test_s4_push_script_has_valid_zsh_syntax():
    result = subprocess.run(["zsh", "-n", str(ROOT / "scripts/push_local.sh")], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_t1_chatgpt_ads_scoring_weights_and_prompt_are_vertical():
    config = RadarConfig.load(ROOT)
    assert config.scoring["score_profile"] == "chatgpt_ads_v1"
    assert config.scoring["weights"]["acquisition"] == {"p0": 0.65, "p1": 0.35}
    assert config.scoring["weights"]["ecosystem"] == {
        "platform_presence": 0.55,
        "channel_ecosystem": 0.45,
    }

    prompt = score_prompt(
        [{"name": "出海品牌增长大会", "date_start": "2026-09-01", "city": "上海"}],
        config.scoring,
    )
    assert "ChatGPT Ads 潜在买家密度" in prompt
    assert "OpenAI 官方 > 其他 AI 平台 > 广告平台" in prompt
    assert "海外营销代理商、出海服务商、跨境物流/支付" in prompt
    assert "这场会对卖 ChatGPT Ads 有什么用" in prompt
    assert "新能源" not in prompt
    assert "AI 开发者" not in prompt
    assert "星图比特" not in prompt


def test_t2_rescore_active_expected_preserves_old_scores_and_status(tmp_path, monkeypatch):
    config = RadarConfig.load(isolated_root(tmp_path))
    events = [
        Event(
            id="active",
            name="出海广告主大会",
            date_start="2026-09-01",
            city="上海",
            url="https://example.com/active",
            status="active",
            acquisition_score=5,
            ecosystem_score=4,
            tier="C",
            reason="旧语义理由。",
            last_verified="2026-08-20T00:00:00+00:00",
        ),
        Event(
            id="expected",
            name="出海营销渠道峰会",
            date_start="2026-10-01",
            city="上海",
            url="https://example.com/expected",
            status="expected",
            acquisition_score=4,
            ecosystem_score=5,
            tier="C",
            reason="旧语义理由。",
            score_history=[{"timestamp": "old", "acquisition_score": 4, "ecosystem_score": 5}],
        ),
        Event(
            id="cancelled",
            name="已取消活动",
            date_start="2026-09-02",
            city="上海",
            url="https://example.com/cancelled",
            status="cancelled",
            acquisition_score=9,
            ecosystem_score=9,
            tier="A",
        ),
        Event(
            id="changed",
            name="出海渠道交流会",
            date_start="2026-09-03",
            city="上海",
            url="https://example.com/changed",
            status="changed",
            acquisition_score=6,
            ecosystem_score=5,
            tier="B",
            reason="旧语义理由。",
        ),
    ]

    def fake_score(_config, rows):
        assert all("acquisition_score" not in row for row in rows)
        assert all("ecosystem_score" not in row for row in rows)
        assert all("reason" not in row for row in rows)
        return [
            {
                **row,
                "acquisition_score": 9 if row["id"] == "active" else 3,
                "ecosystem_score": 6 if row["id"] == "active" else 8,
                "audience_side": "demand" if row["id"] == "active" else "supply",
                "scale_hint": "large",
                "format": "open",
                "action": "attend" if row["id"] == "active" else "send_colleague",
                "reason": (
                    "出海广告主和品牌 marketing 负责人会在场。适合带报价单现场约深聊。"
                    if row["id"] == "active"
                    else "海外营销代理商和出海服务商会在场。适合认识可分销的代理商。"
                ),
            }
            for row in rows
        ]

    monkeypatch.setattr("activity_radar.research._score_candidates", fake_score)
    rescored, stats = rescore_active_events(config, events)
    by_id = {event.id: event for event in rescored}

    assert stats["scoring_result"] == "hit"
    assert stats["target_count"] == 3
    assert stats["rescored_count"] == 3
    assert stats["unscored_event_count"] == 0
    assert stats["before_tiers"] == {"B": 1, "C": 2}
    assert stats["after_tiers"] == {"A": 3}
    assert {row["id"] for row in stats["tier_changes"]} == {"active", "expected", "changed"}
    assert by_id["active"].status == "active"
    assert by_id["expected"].status == "expected"
    assert by_id["cancelled"].status == "cancelled"
    assert by_id["changed"].status == "changed"
    assert (by_id["active"].score_history[-2]["acquisition_score"], by_id["active"].score_history[-2]["ecosystem_score"]) == (5, 4)
    assert by_id["active"].score_history[-1]["score_profile"] == "chatgpt_ads_v1"
    assert by_id["expected"].score_history[0] == {"timestamp": "old", "acquisition_score": 4, "ecosystem_score": 5}
    assert by_id["expected"].score_history[-1]["score_profile"] == "chatgpt_ads_v1"
    assert by_id["active"].metadata["score_profile"] == "chatgpt_ads_v1"
    assert by_id["active"].metadata["score_audit"]["final"] == {
        "acquisition_score": 9,
        "ecosystem_score": 6,
    }
    assert read_jsonl(config.root / "data/events-rescore-unscored.jsonl") == []


def test_t2_rescore_skips_events_already_on_current_profile(tmp_path, monkeypatch):
    config = RadarConfig.load(isolated_root(tmp_path))
    event = Event(
        id="done",
        name="已完成垂直重排的活动",
        date_start="2026-09-01",
        city="上海",
        url="https://example.com/done",
        status="changed",
        metadata={"score_profile": "chatgpt_ads_v1"},
    )
    monkeypatch.setattr(
        "activity_radar.research._score_candidates",
        lambda *_args: pytest.fail("current-profile event must not be sent to the LLM again"),
    )

    rescored, stats = rescore_active_events(config, [event])

    assert rescored[0].to_dict() == event.to_dict()
    assert stats["scoring_result"] == "empty"
    assert stats["target_count"] == 0
    assert stats["skipped_current_profile"] == 1


def test_t2_verified_city_is_not_reclassified_from_sales_reason():
    scoring = RadarConfig.load(ROOT).scoring
    event = prepare_event(
        {
            "id": "shanghai",
            "name": "出海营销渠道峰会",
            "date_start": "2026-09-01",
            "city": "上海",
            "url": "https://example.com/shanghai",
            "acquisition_score": 6,
            "ecosystem_score": 6,
            "reason": "海外营销代理商和出海服务商会在场。适合认识可分销的代理商。",
        },
        "test",
        "now",
        scoring,
    )

    assert event.city == "上海"
    assert (event.acquisition_score, event.ecosystem_score) == (6, 6)


def test_t2_merge_never_deletes_existing_history_even_if_now_out_of_scope():
    scoring = RadarConfig.load(ROOT).scoring
    existing = Event(
        id="legacy",
        name="跨境合规专场·许昌站",
        date_start="",
        city="上海",
        url="https://example.com/legacy",
        status="active",
        tier="D",
        acquisition_score=2,
        ecosystem_score=1,
        reason="产业带出海企业会在场。适合先核对名单。",
        score_history=[{"timestamp": "old", "acquisition_score": 5, "ecosystem_score": 4}],
        metadata={"score_profile": "chatgpt_ads_v1"},
    )

    merged, _stats = merge_events([existing], [], {"上海"}, scoring)

    assert [event.id for event in merged] == ["legacy"]
    assert merged[0].score_history == existing.score_history


def test_t3_chatgpt_ads_rerank_push_uses_exact_title_and_score_order(tmp_path):
    config = RadarConfig.load(isolated_root(tmp_path))
    events = [
        Event(id="lower", name="稍低分大会", date_start="2026-08-20", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="出海广告主会在场。适合带报价单深聊。", url="https://example.com/lower"),
        Event(id="higher", name="更高分大会", date_start="2026-08-22", city="上海", tier="A", acquisition_score=9, ecosystem_score=8, reason="品牌 marketing 负责人会在场。适合现场约深聊。", url="https://example.com/higher"),
        Event(id="channel", name="渠道生态大会", date_start="2026-08-21", city="上海", tier="A", acquisition_score=7, ecosystem_score=9, reason="海外营销代理商会在场。适合认识可分销的代理商。", url="https://example.com/channel"),
    ]

    message = build_push_for_config(
        events,
        config,
        today=date(2026, 8, 19),
        mode="full",
        chatgpt_ads_rerank=True,
    )

    assert message.splitlines()[0] == "📡 活动雷达｜ChatGPT Ads 垂直重排版"
    assert message.index("更高分大会") < message.index("渠道生态大会") < message.index("稍低分大会")
    assert "品牌 marketing 负责人会在场。" in message
    assert "海外营销代理商会在场。" in message


def test_t3_cli_writes_special_full_push_as_dry_run(tmp_path, capsys):
    root = isolated_root(tmp_path)
    event = Event(id="a", name="ChatGPT Ads 客户大会", date_start="2026-09-01", city="上海", tier="A", acquisition_score=9, ecosystem_score=8, reason="出海广告主会在场。适合带报价单现场约深聊。", url="https://example.com/a")
    (root / "data/events.jsonl").write_text(json.dumps(event.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")

    assert main(["--root", str(root), "push", "--mode", "full", "--chatgpt-ads-rerank"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "dry_run"
    assert result["artifacts"]["history"].endswith("-chatgpt-ads-rerank.txt")
    assert (root / "data/push-latest.txt").read_text(encoding="utf-8").startswith("📡 活动雷达｜ChatGPT Ads 垂直重排版\n")
