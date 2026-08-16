from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import load_structured


@dataclass
class RadarConfig:
    root: Path
    sources: list[dict[str, Any]]
    scoring: dict[str, Any]
    events_path: Path
    logs_path: Path
    site_path: Path
    push_target: str = "weixin"
    model: str = "gpt-5.5"
    base_url: str = "https://api.openai.com/v1"

    @classmethod
    def load(cls, root: Path) -> "RadarConfig":
        sources_data = load_structured(root / "config/sources.yaml") or {}
        scoring = load_structured(root / "config/scoring.yaml") or {}
        return cls(
            root=root,
            sources=sources_data.get("sources", []),
            scoring=scoring,
            events_path=root / "data/events.jsonl",
            logs_path=root / "logs/run.jsonl",
            site_path=root / "site/index.html",
            push_target=os.getenv("RADAR_PUSH_TARGET", "weixin"),
            model=os.getenv("RADAR_MODEL", "gpt-5.5"),
            base_url=os.getenv("CODEX_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
        )

    @property
    def source_health_path(self) -> Path:
        return self.root / "data/source-health.json"

    @property
    def city_scope(self) -> set[str]:
        return set(self.scoring.get("city_scope", ["上海", "Shanghai"]))

    @property
    def webinar_in_push(self) -> bool:
        return bool(self.scoring.get("webinar_in_push", False))
