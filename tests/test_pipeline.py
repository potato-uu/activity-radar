import json
import shutil
import threading
import time
from pathlib import Path

from activity_radar.cli import main, update_source_health
from activity_radar.config import RadarConfig
from activity_radar.io import read_json, read_jsonl
from activity_radar.provider import Usage
from activity_radar.push import build_push_for_config
from activity_radar.render import render_timeline
from activity_radar.research import discover_and_score
from activity_radar.schema import Event


ROOT = Path(__file__).resolve().parents[1]


def isolated_root(tmp_path: Path) -> Path:
    for name in ("config", "fixtures"):
        shutil.copytree(ROOT / name, tmp_path / name)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/events.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def test_fixture_run_produces_timeline_and_events(tmp_path, capsys):
    root = isolated_root(tmp_path)
    assert main(["--root", str(root), "run", "--fixture", str(root / "fixtures/sample_candidates.json")]) == 0
    rows = read_jsonl(root / "data/events.jsonl")
    assert len(rows) == 16
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


def test_push_keeps_webinar_out(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    webinar = Event(id="w", name="webinar", date_start="2026-09-01", city="上海", event_type="webinar", tier="A", acquisition_score=9, ecosystem_score=9, reason="a\nb", url="https://example.com")
    assert "webinar" not in build_push_for_config([webinar], config, today=__import__("datetime").date(2026, 8, 16))


def test_live_discovery_runs_sources_concurrently_and_classifies_timeout(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.sources = config.sources[:2]
    config.source_timeout_seconds = 222
    config.source_retries = 1
    config.discovery_concurrency = 2

    barrier = threading.Barrier(2)
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    seen_timeouts = []
    seen_retries = []

    class FakeClient:
        def __init__(self, base_url, model, client_root, timeout=180):
            seen_timeouts.append(timeout)

        def request(self, prompt, *, web_search=True, retries=3):
            seen_retries.append(retries)
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
    events, stats = discover_and_score(config, live=True)

    assert events == []
    assert stats["source_errors"] == ["huodongxing"]
    assert stats["source_error_details"] == {"huodongxing": "timeout"}
    assert state["max_active"] == 2
    assert seen_timeouts == [222, 222]
    assert seen_retries == [1, 1]


def test_source_timeout_does_not_count_as_no_hit(tmp_path):
    root = isolated_root(tmp_path)
    config = RadarConfig.load(root)
    config.sources = config.sources[:1]

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
    assert row["last_result"] == "timeout"
    assert row["last_error_kind"] == "timeout"

    update_source_health(
        config,
        {"source_hits": [], "source_errors": [], "source_error_details": {}},
    )
    row = read_json(config.source_health_path, {})["huodongxing"]
    assert row["no_hit_runs"] == 1
    assert row["last_result"] == "empty"
    assert "last_error_kind" not in row
