from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import RadarConfig
from .io import append_jsonl
from .provider import OpenAIResponsesClient, ProviderError, Usage, parse_json_text
from .rules import prepare_event
from .schema import Event


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discover_prompt(source: dict[str, Any], today: date, city_scope: set[str]) -> str:
    schema = {
        "name": "string",
        "name_en": "string",
        "date_start": "YYYY-MM-DD",
        "date_end": "YYYY-MM-DD",
        "city": "上海",
        "venue": "string",
        "organizer": "string",
        "url": "official or registration URL",
        "ticket_price": "string",
        "register_deadline": "YYYY-MM-DD or empty",
        "event_type": "展会|峰会|沙龙|开发者大会|webinar",
        "source": source["id"],
    }
    return f"""你是 BD 活动雷达研究员。今天是 {today.isoformat()}。只研究未来 60 天、城市为上海的真实活动；来源优先使用官方页面，搜索摘要不能单独作为证据。信息源：{source['name']} ({source['url']})。扫描要求：{source.get('instruction', '')}

只返回 JSON 数组，不要 Markdown，不要解释。每条必须包含：{json.dumps(schema, ensure_ascii=False)}。找不到可靠官方活动时返回 []。不要把线上 webinar 当作线下上海活动，不要编造日期、价格、截止日或 URL。"""


def score_prompt(candidates: list[dict[str, Any]], scoring: dict[str, Any]) -> str:
    return f"""你是活动评分员。按以下规则给每条活动独立打 acquisition_score 和 ecosystem_score（0-10），不合并：获客线看 P0 海外投放/出海营销、P1 独立站/亚马逊/Shopify、P2 新能源出海；资源线看平台方出席深度、AI 应用开发者密度、渠道/代理/服务商生态。城市范围只有上海。Tier：任一线>=8 为 A，>=6 为 B，>=4 为 C，双线<4 丢弃；webinar 默认网页不推送。reason 必须是两句中文，说明谁会在场以及为什么与星图比特相关。action 只能是 attend/send_colleague/watch_content/host_side_event。

配置：{json.dumps(scoring, ensure_ascii=False)}
候选：{json.dumps(candidates, ensure_ascii=False)}

只返回 JSON 数组，保留候选的所有原字段并补全 acquisition_score、ecosystem_score、tier、action、reason。不得编造候选不存在的事实。"""


def _log(config: RadarConfig, payload: dict[str, Any]) -> None:
    append_jsonl(config.logs_path, {"timestamp": now_iso(), **payload})


def _error_kind(exc: Exception) -> str:
    message = str(exc).lower()
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "authentication failed" in message or "no codex_api_key" in message:
        return "authentication"
    return "error"


def _discover_source(
    config: RadarConfig, source: dict[str, Any], today: date
) -> tuple[list[dict[str, Any]], Usage, str | None, str | None]:
    """Run one source in isolation so a slow source cannot block its peers."""
    client = OpenAIResponsesClient(
        config.base_url,
        config.model,
        config.root,
        timeout=config.source_timeout_seconds,
    )
    try:
        text, usage = client.request(
            discover_prompt(source, today, config.city_scope),
            retries=config.source_retries,
        )
        rows = parse_json_text(text)
        if not isinstance(rows, list):
            raise ProviderError(f"Source {source['id']} did not return a JSON array")
        return rows, usage, None, None
    except Exception as exc:
        return [], Usage(), _error_kind(exc), str(exc)


def discover_and_score(config: RadarConfig, *, fixture: Path | None = None, live: bool = False) -> tuple[list[Event], dict[str, Any]]:
    now = now_iso()
    if fixture is not None:
        fixture_data = json.loads(fixture.read_text(encoding="utf-8"))
        candidates = fixture_data.get("events", fixture_data) if isinstance(fixture_data, dict) else fixture_data
        events = [prepare_event(item, item.get("source", "fixture"), now, config.scoring) for item in candidates]
        hit_sources = sorted({event.source for event in events})
        _log(config, {"kind": "fixture", "source_count": 1, "candidate_count": len(events), "usage": Usage().to_dict(), "api_cost": 0})
        return events, {"source_count": 1, "candidate_count": len(events), "api_cost": 0, "source_hits": hit_sources, "source_errors": []}
    if not live:
        raise ProviderError("Live research is disabled. Pass --live or provide --fixture.")
    discovered: list[dict[str, Any]] = []
    source_count = 0
    source_hits: list[str] = []
    source_errors: list[str] = []
    source_error_details: dict[str, str] = {}
    total_cost = 0.0
    enabled_sources = [source for source in config.sources if source.get("enabled", True)]
    source_count = len(enabled_sources)
    max_workers = min(config.discovery_concurrency, max(1, source_count))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="radar-source") as pool:
        futures = {
            source["id"]: pool.submit(_discover_source, config, source, date.today())
            for source in enabled_sources
        }
        for source in enabled_sources:
            rows, usage, error_kind, error_message = futures[source["id"]].result()
            if error_message:
                source_errors.append(source["id"])
                source_error_details[source["id"]] = error_kind or "error"
                _log(
                    config,
                    {
                        "kind": "discover_error",
                        "source": source["id"],
                        "error_kind": error_kind or "error",
                        "error": error_message,
                    },
                )
                if error_kind == "authentication":
                    raise ProviderError(error_message)
                continue
            discovered.extend(rows)
            if rows:
                source_hits.append(source["id"])
            if usage.cost_usd is not None:
                total_cost += usage.cost_usd
            _log(config, {"kind": "discover", "source": source["id"], "candidate_count": len(rows), "usage": usage.to_dict(), "api_cost": usage.cost_usd})
    scored: list[dict[str, Any]] = []
    if discovered:
        client = OpenAIResponsesClient(
            config.base_url,
            config.model,
            config.root,
            timeout=config.source_timeout_seconds,
        )
        text, usage = client.request(score_prompt(discovered, config.scoring), web_search=False)
        value = parse_json_text(text)
        if not isinstance(value, list):
            raise ProviderError("Scoring response was not a JSON array")
        scored = value
        if usage.cost_usd is not None:
            total_cost += usage.cost_usd
        _log(config, {"kind": "score", "candidate_count": len(scored), "usage": usage.to_dict(), "api_cost": usage.cost_usd})
    events = [prepare_event(item, item.get("source", "unknown"), now, config.scoring) for item in scored]
    _log(config, {"kind": "run_summary", "source_count": source_count, "candidate_count": len(events), "api_cost": total_cost if total_cost else None, "api_cost_status": "logged_unknown" if total_cost == 0 else "logged"})
    return events, {
        "source_count": source_count,
        "candidate_count": len(events),
        "api_cost": total_cost or None,
        "source_hits": source_hits,
        "source_errors": source_errors,
        "source_error_details": source_error_details,
    }
