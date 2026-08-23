import json
import shutil
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from activity_radar.adapters.generic import extract_html_candidates
from activity_radar.cli import main, update_source_health
from activity_radar.config import RadarConfig
from activity_radar.io import read_json, read_jsonl
from activity_radar.provider import Usage
from activity_radar.push import auto_mode, build_push_for_config, has_successful_auto_run, record_auto_success, send_via_hermes, split_message
from activity_radar.render import render_timeline
from activity_radar.research import _score_and_prepare, _score_candidates_in_batches
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
    assert main(["--root", str(root), "run", "--fixture", str(root / "fixtures/sample_candidates.json")]) == 0
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
    assert [r["status"] for r in rows] == ["retrying", "retrying", "failed"]
    assert all(r["chunk"] == 2 for r in rows)


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
        {"name": "报名中", "date_start": "2026-08-20", "city": "上海", "source": "cifnews-ccee"},
        {"name": "eMAG2026中国卖家峰会—深圳", "date_start": "2026-08-20", "city": "深圳", "source": "cifnews-ccee"},
        {"name": "Google 官方深圳开发者大会", "date_start": "2026-09-01", "city": "深圳", "source": "google-developers-cn"},
        {"name": "Shanghai AI Growth Summit", "date_start": "2026-09-02", "city": "上海", "source": "onepilot"},
        {"name": "海外品牌峰会", "date_start": "2026-09-03", "city": "海外", "source": "calendar-seed"},
    ]
    kept, counts = prefilter_candidates(rows, scoring)
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


def test_auto_mode_catches_up_later_same_day():
    """A missed exact hour still sends later the same Shanghai day."""
    late_sunday = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)  # 23:00 Shanghai Sunday
    late_wednesday = datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc)  # 15:00 Shanghai Wednesday
    before_window = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)  # 17:00 Shanghai Sunday
    assert auto_mode(late_sunday) == "full"
    assert auto_mode(late_wednesday) == "delta"
    assert auto_mode(before_window) is None
