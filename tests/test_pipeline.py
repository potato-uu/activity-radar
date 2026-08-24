import json
import shutil
import threading
import time
from datetime import date
from pathlib import Path

from activity_radar.cli import main, update_source_health
from activity_radar.config import RadarConfig
from activity_radar.io import read_json, read_jsonl
from activity_radar.provider import Usage
from activity_radar.provider import ProviderError
from activity_radar.push import build_push_for_config
from activity_radar.render import render_timeline
from activity_radar.research import _error_kind, discover_and_score
from activity_radar.schema import Event


ROOT = Path(__file__).resolve().parents[1]


def isolated_root(tmp_path: Path) -> Path:
    for name in ("config", "fixtures"):
        shutil.copytree(ROOT / name, tmp_path / name)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/events.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def test_sandbox_browser_launch_and_http_403_are_blocked():
    assert _error_kind(RuntimeError("bootstrap_check_in MachPortRendezvousServer: Permission denied (1100)")) == "blocked"
    assert _error_kind(RuntimeError("HTTP Error 403: Forbidden")) == "blocked"


def test_fixture_run_produces_timeline_and_events(tmp_path, capsys):
    root = isolated_root(tmp_path)
    assert main(["--root", str(root), "run", "--fixture", str(root / "fixtures/sample_candidates.json"), "--as-of", "2027-08-18"]) == 0
    rows = read_jsonl(root / "data/events.jsonl")
    # H2: candidates whose parsed start date is before today are excluded before scoring.
    assert len(rows) == 15
    assert all(row["tier"] != "D" for row in rows)
    assert (root / "site/index.html").exists()
    output = capsys.readouterr().out
    assert '"status": "dry_run"' in output


def test_google_backtest_meets_acceptance(tmp_path, capsys):
    root = isolated_root(tmp_path)
    assert main(["--root", str(root), "backtest", "--fixture", str(root / "fixtures/google-developer-backtest.json"), "--as-of", "2026-07-01"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["captured"] is True
    assert result["matches"][0]["ecosystem_score"] >= 8


def test_push_includes_high_value_webinar_in_v2(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    webinar = Event(id="w", name="AI Strategy Webinar", date_start="2026-09-01", city="上海", event_type="webinar", tier="A", acquisition_score=9, ecosystem_score=9, reason="a\nb", url="https://example.com")
    assert "AI Strategy Webinar" in build_push_for_config([webinar], config, today=__import__("datetime").date(2026, 8, 16))


def test_live_discovery_runs_sources_concurrently_and_classifies_timeout(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.sources = [
        {"id": "huodongxing", "name": "活动行", "adapter": "llm_sweep", "url": "https://www.huodongxing.com/", "instruction": "活动行"},
        {"id": "luma-shanghai-ai", "name": "Luma Shanghai AI", "adapter": "llm_sweep", "url": "https://luma.com/shanghai", "instruction": "Luma"},
    ]
    config.source_timeout_seconds = 222
    config.source_retries = 1
    config.discovery_concurrency = 2

    barrier = threading.Barrier(2)
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    seen_timeouts = []
    seen_retries = []
    seen_prompts = []

    class FakeClient:
        def __init__(self, base_url, model, client_root, timeout=180):
            seen_timeouts.append(timeout)

        def request(self, prompt, *, web_search=True, retries=3):
            seen_retries.append(retries)
            seen_prompts.append(prompt)
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            barrier.wait(timeout=1)
            time.sleep(0.01)
            with lock:
                state["active"] -= 1
            if "活动行" in prompt:
                raise TimeoutError("The read operation timed out")
            return "[]", Usage()

    monkeypatch.setattr("activity_radar.research.OpenAIResponsesClient", FakeClient)
    events, stats = discover_and_score(config, live=True, as_of=date(2027, 5, 4))

    assert events == []
    assert stats["source_errors"] == ["huodongxing"]
    assert stats["source_error_details"] == {"huodongxing": "timeout"}
    assert state["max_active"] == 2
    assert seen_timeouts == [222, 222]
    assert seen_retries == [1, 1]
    assert all("今天是 2027-05-04" in prompt for prompt in seen_prompts)


def test_source_timeout_does_not_count_as_no_hit(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.sources = [source for source in config.sources if source["id"] == "huodongxing"]

    update_source_health(
        config,
        {
            "source_hits": [],
            "source_errors": ["huodongxing"],
            "source_error_details": {"huodongxing": "timeout"},
        },
    )
    row = read_json(config.source_health_path, {})["huodongxing"]
    assert row["no_hit_runs"] == 0
    assert row["scan_count"] == 1
    assert row["last_result"] == "timeout"
    assert row["last_error_kind"] == "timeout"
    assert row["reason"] == "timeout"

    update_source_health(
        config,
        {"source_hits": [], "source_errors": [], "source_error_details": {}},
    )
    row = read_json(config.source_health_path, {})["huodongxing"]
    assert row["no_hit_runs"] == 1
    assert row["scan_count"] == 2
    assert row["last_result"] == "empty"
    assert row["reason"] == "no candidates in the 120-day window"
    assert "last_error_kind" not in row


def test_source_health_does_not_touch_unscanned_sources(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.sources = [source for source in config.sources if source["id"] in {"onepilot", "huodongxing"}]

    update_source_health(
        config,
        {
            "source_ids": ["onepilot"],
            "source_hits": ["onepilot"],
            "source_errors": [],
            "source_error_details": {},
        },
    )

    health = read_json(config.source_health_path, {})
    assert health["onepilot"]["last_result"] == "hit"
    assert health["onepilot"]["reason"] == "1 candidates"
    assert "huodongxing" not in health


def test_direct_adapters_feed_normalization_without_llm(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    (root / "config/sources.yaml").write_text(
        """
sources:
  - id: onepilot
    name: OnePilot
    adapter: api
    enabled: true
    url: https://onepilot.xin/supabase-config.js
  - id: calendar-seed
    name: Calendar
    adapter: calendar_seed
    enabled: true
    params: {path: config/annual_calendar.yaml}
""".strip(),
        encoding="utf-8",
    )
    (root / "config/annual_calendar.yaml").write_text(
        """
events:
  - id: seed
    name: Seed Summit
    typical_month: 9
    city: 上海
    official_url: https://example.com/seed
    why_it_matters: P0
""".strip(),
        encoding="utf-8",
    )

    class FakeAdapter:
        def fetch(self, source, window):
            from activity_radar.adapters.base import RawCandidate

            if source["id"] == "onepilot":
                return [RawCandidate("onepilot", "API Meetup", "2026-09-01", "https://example.com/api", "now", city="上海", raw_excerpt="meetup")]
            return [RawCandidate("calendar-seed", "Seed Summit", "2026-09-01", "https://example.com/seed", "now", city="上海", raw_excerpt="P0", date_start="2026-09-01", date_end="2026-09-01", status="expected", date_precision="month")]

    monkeypatch.setattr("activity_radar.research.get_adapter", lambda source: FakeAdapter())
    monkeypatch.setattr("activity_radar.research._score_candidates", lambda config, rows: rows)
    config = RadarConfig.load(root)
    events, stats = discover_and_score(config, live=True, source_ids=["onepilot", "calendar-seed"])

    assert {event.name for event in events} == {"API Meetup", "Seed Summit"}
    assert stats["source_hits"] == ["calendar-seed", "onepilot"]
    assert stats["source_errors"] == []


def test_direct_adapter_hits_survive_unavailable_scoring(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    (root / "config/sources.yaml").write_text(
        """
sources:
  - id: onepilot
    name: OnePilot
    adapter: api
    enabled: true
    url: https://onepilot.xin/supabase-config.js
""".strip(),
        encoding="utf-8",
    )

    class FakeAdapter:
        def fetch(self, source, window):
            from activity_radar.adapters.base import RawCandidate

            return [RawCandidate("onepilot", "API Meetup", "2026-09-01", "https://example.com/api", "now", date_start="2026-09-01", date_end="2026-09-01", city="上海")]

    monkeypatch.setattr("activity_radar.research.get_adapter", lambda source: FakeAdapter())
    monkeypatch.setattr("activity_radar.research._score_candidates", lambda config, rows: (_ for _ in ()).throw(ProviderError("HTTP 401")))
    events, stats = discover_and_score(RadarConfig.load(root), live=True, source_ids=["onepilot"])

    assert events == []
    assert stats["source_hits"] == ["onepilot"]
    assert stats["scoring_result"] == "unavailable"
    assert stats["unscored_candidate_count"] == 1


def test_llm_sweep_auth_failure_is_unavailable_not_fatal(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.sources = [{"id": "llm-sweep", "name": "LLM sweep", "adapter": "llm_sweep", "url": "https://example.com", "enabled": True}]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            raise ProviderError("Responses API authentication failed with HTTP 401")

    monkeypatch.setattr("activity_radar.research.OpenAIResponsesClient", FakeClient)
    events, stats = discover_and_score(config, live=True)

    assert events == []
    assert stats["source_errors"] == ["llm-sweep"]
    assert stats["source_error_details"] == {"llm-sweep": "unavailable"}


def test_mixed_direct_and_llm_sources_survive_unavailable_scoring(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.sources = [
        {"id": "direct", "name": "Direct", "adapter": "html_list", "url": "https://example.com", "enabled": True},
        {"id": "llm-sweep", "name": "LLM", "adapter": "llm_sweep", "url": "https://example.com", "enabled": True},
    ]

    class FakeAdapter:
        def fetch(self, source, window):
            from activity_radar.adapters.base import RawCandidate

            return [RawCandidate("direct", "Direct Event", "2026-09-01", "https://example.com/event", "now", date_start="2026-09-01", date_end="2026-09-01", city="上海")]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            raise ProviderError("Responses API authentication failed with HTTP 401")

    monkeypatch.setattr("activity_radar.research.get_adapter", lambda source: FakeAdapter())
    monkeypatch.setattr("activity_radar.research.OpenAIResponsesClient", FakeClient)
    events, stats = discover_and_score(config, live=True)

    assert events == []
    assert stats["source_hits"] == ["direct"]
    assert stats["source_error_details"]["llm-sweep"] == "unavailable"
    assert stats["scoring_result"] == "unavailable"
    assert stats["unscored_candidate_count"] == 1


def test_push_full_and_delta_include_v2_sections_and_history(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    events = [
        Event(id="a", name="Expected Summit", date_start="2026-08-20", city="上海", event_type="峰会", tier="A", acquisition_score=8, ecosystem_score=8, reason="需求侧明确。平台方在场。", url="https://example.com/a", status="expected", first_seen="2026-08-18T00:00:00+00:00", metadata={"is_series": True, "occurrences": ["2026-08-20", "2026-09-03"]}, is_series=True, occurrences=["2026-08-20", "2026-09-03"]),
        Event(id="b", name="Side Dinner", date_start="2026-08-21", city="上海", event_type="side_event", tier="B", acquisition_score=6, ecosystem_score=6, reason="需求侧明确。关联大会。", url="https://example.com/b", status="changed", related_to="a", first_seen="2026-08-18T00:00:00+00:00"),
    ]
    (root / "data/source-health.json").write_text('{"wechat-search":{"no_hit_runs":0,"last_result":"unavailable","reason":"miku_ai unavailable"}}\n', encoding="utf-8")
    full = build_push_for_config(events, config, today=date(2026, 8, 18), mode="full")
    delta = build_push_for_config(events, config, today=date(2026, 8, 18), mode="delta")
    assert "日期待官宣" in full
    assert "每周，下一场" in full
    assert "🎪 side event 机会" not in full
    assert "unavailable" in full
    assert "✏️ 变更" in delta
    assert "Side Dinner" in delta


def test_timeline_renders_v2_filters_and_event_badges(tmp_path):
    output = tmp_path / "site/index.html"
    event = Event(
        id="a",
        name="Expected Summit",
        date_start="2026-08-20",
        city="杭州",
        event_type="side_event",
        tier="A",
        acquisition_score=8,
        ecosystem_score=8,
        reason="reason",
        url="https://example.com/a",
        status="expected",
        is_series=True,
        occurrences=["2026-08-20", "2026-09-03"],
        related_to="conference-id",
        needs_review=True,
    )
    render_timeline([event], output, "2026-08-18T00:00:00+00:00")
    html = output.read_text(encoding="utf-8")
    assert 'id="city"' in html
    assert "side_event" in html
    assert "日期待官宣" in html
    assert "系列" in html
    assert "related_to" in html
    assert "needs_review" in html


def test_timeline_omits_cancelled_events(tmp_path):
    output = tmp_path / "site/index.html"
    active = Event(id="active", name="Active Summit", date_start="2026-09-01", city="上海", tier="B", acquisition_score=6, ecosystem_score=5, reason="active", url="https://example.com/active")
    cancelled = Event(id="cancelled", name="Cancelled Summit", date_start="2026-09-02", city="上海", tier="A", acquisition_score=8, ecosystem_score=8, reason="cancelled", url="https://example.com/cancelled", status="cancelled")
    archived_d = Event(id="archived-d", name="Archived D Meetup", date_start="2026-09-03", city="上海", tier="D", acquisition_score=3, ecosystem_score=2, reason="archived", url="https://example.com/archived")

    render_timeline([active, cancelled, archived_d], output, "2026-08-18T00:00:00+00:00")

    page = output.read_text(encoding="utf-8")
    assert "Active Summit" in page
    assert "Cancelled Summit" not in page
    assert "Archived D Meetup" not in page


def test_live_source_backtest_uses_adapter_window(tmp_path, monkeypatch, capsys):
    root = isolated_root(tmp_path)
    observed = {}

    def fake_discover(config, **kwargs):
        observed.update(kwargs)
        return [], {"source_hits": ["onepilot"], "source_errors": [], "source_ids": ["onepilot"]}

    monkeypatch.setattr("activity_radar.cli.discover_and_score", fake_discover)
    assert main(["--root", str(root), "backtest", "--as-of", "2026-07-01", "--live-source", "onepilot"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert observed["source_ids"] == ["onepilot"]
    assert observed["as_of"] == date(2026, 7, 1)
    assert result["captured"] is False
