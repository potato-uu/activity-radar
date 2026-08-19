from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import load_structured
from .provider import load_local_env


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


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
    source_timeout_seconds: int = 180
    source_retries: int = 2
    discovery_concurrency: int = 3
    window_days: int = 120

    @classmethod
    def load(cls, root: Path) -> "RadarConfig":
        # Load project-local settings before resolving any environment-backed fields.
        load_local_env(root)
        sources_data = load_structured(root / "config/sources.yaml") or {}
        scoring = load_structured(root / "config/scoring.yaml") or {}
        return cls(
            root=root,
            sources=[{**source, "_root": str(root)} for source in sources_data.get("sources", [])],
            scoring=scoring,
            events_path=root / "data/events.jsonl",
            logs_path=root / "logs/run.jsonl",
            site_path=root / "site/index.html",
            push_target=os.getenv("RADAR_PUSH_TARGET", "weixin"),
            model=os.getenv("RADAR_MODEL", "gpt-5.5"),
            base_url=os.getenv("CODEX_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
            source_timeout_seconds=_env_int(
                "RADAR_SOURCE_TIMEOUT_SECONDS", 180, minimum=30, maximum=900
            ),
            source_retries=_env_int("RADAR_SOURCE_RETRIES", 2, minimum=1, maximum=3),
            discovery_concurrency=_env_int(
                "RADAR_DISCOVERY_CONCURRENCY", 3, minimum=1, maximum=6
            ),
            window_days=_env_int("RADAR_WINDOW_DAYS", 120, minimum=30, maximum=365),
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
