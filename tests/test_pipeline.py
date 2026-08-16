import json
import shutil
from pathlib import Path

from activity_radar.cli import main
from activity_radar.config import RadarConfig
from activity_radar.io import read_jsonl
from activity_radar.push import build_push_for_config
from activity_radar.render import render_timeline
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
