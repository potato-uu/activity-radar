from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .config import RadarConfig
from .adapters import AdapterWindow
from .adapters.registry import get_adapter
from .io import append_jsonl, read_jsonl, write_jsonl
from .provider import OpenAIResponsesClient, ProviderError, Usage, parse_json_text
from .rules import prefilter_candidates, prepare_event
from .schema import Event


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discover_prompt(source: dict[str, Any], today: date, city_scope: set[str]) -> str:
    schema = {
        "name": "string",
        "name_en": "string",
        "date_start": "YYYY-MM-DD",
        "date_end": "YYYY-MM-DD",
        "city": "上海|杭州|苏州|南京|宁波|无锡|合肥|嘉兴|南通|其他国内",
        "venue": "string",
        "organizer": "string",
        "url": "official or registration URL",
        "ticket_price": "string",
        "register_deadline": "YYYY-MM-DD or empty",
        "event_type": "展会|峰会|沙龙|开发者大会|webinar",
        "source": source["id"],
    }
    return f"""你是 BD 活动雷达研究员。今天是 {today.isoformat()}。只研究未来 120 天的真实活动。地域：上海主场；周边为杭州、苏州、南京、宁波、无锡、合肥、嘉兴、南通；其他国内城市只有明确 Tier A 潜力才返回；海外不返回。来源优先使用官方页面，搜索摘要不能单独作为证据。信息源：{source['name']} ({source['url']})。扫描要求：{source.get('instruction', '')}

只返回 JSON 数组，不要 Markdown，不要解释。每条必须包含：{json.dumps(schema, ensure_ascii=False)}。找不到可靠官方活动时返回 []。不要把线上 webinar 当作线下上海活动，不要编造日期、价格、截止日或 URL。"""


def score_prompt(candidates: list[dict[str, Any]], scoring: dict[str, Any]) -> str:
    return f"""你是活动评分员。temperature 必须不高于 0.2。按以下规则给每条活动独立打 acquisition_score 和 ecosystem_score（0-10），不合并：获客线看需求侧 ICP 密度，P0 为出海投放操盘手、广告主、出海品牌 marketing 负责人和 GEO 的品牌方客户，P1 为独立站/亚马逊/Shopify 创始人，P2 为新能源/光伏出海负责人；资源线看平台方官方在场深度 0.55、渠道生态 0.35、AI 开发者 0.10。不要因为 GEO 服务商或同类工具商多而提高获客分。reason 必须是两句中文，说明谁会在场以及为什么与星图比特相关。action 只能是 attend/send_colleague/watch_content/host_side_event。

配置：{json.dumps(scoring, ensure_ascii=False)}
候选：{json.dumps(candidates, ensure_ascii=False)}

    只返回 JSON 数组，保留候选的所有原字段并补全 acquisition_score、ecosystem_score、audience_side(demand|supply|mixed)、scale_hint、format(open|closed_door|invite_only)、is_series、action、reason。规则层会负责修正分数上限和 Tier。不得编造候选不存在的事实。"""


def _score_candidates(config: RadarConfig, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    if len(candidates) > 15:
        raise ValueError("score batch exceeds 15 candidates")
    client = OpenAIResponsesClient(
        config.base_url,
        config.model,
        config.root,
        timeout=config.source_timeout_seconds,
    )
    text, usage = client.request(score_prompt(candidates, config.scoring), web_search=False, retries=config.source_retries, temperature=0.2)
    value = parse_json_text(text)
    if not isinstance(value, list):
        raise ProviderError("Scoring response was not a JSON array")
    _log(config, {"kind": "score_batch", "candidate_count": len(candidates), "scored_count": len(value), "usage": usage.to_dict(), "api_cost": usage.cost_usd})
    return value


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("url") or "").strip(),
        str(row.get("name") or row.get("raw_title") or "").strip().lower(),
        str(row.get("date_start") or "")[:10],
    )


def _score_candidates_in_batches(
    config: RadarConfig, candidates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Score bounded batches independently so one failed request is resumable."""
    batches = [candidates[index:index + 15] for index in range(0, len(candidates), 15)]
    if not batches:
        return [], [], []

    def score_one(batch: list[dict[str, Any]], batch_index: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            scored = _score_candidates(config, batch)
            scored_keys = {_candidate_key(row) for row in scored if isinstance(row, dict)}
            pending = [row for row in batch if _candidate_key(row) not in scored_keys]
            return scored, pending, []
        except Exception as exc:
            _log(config, {
                "kind": "score_batch",
                "candidate_count": len(batch),
                "scored_count": 0,
                "status": "failed",
                "error": str(exc)[:500],
                "usage": Usage().to_dict(),
                "api_cost": None,
            })
            if len(batch) > 1:
                midpoint = len(batch) // 2
                left = score_one(batch[:midpoint], batch_index)
                right = score_one(batch[midpoint:], batch_index)
                return (
                    left[0] + right[0],
                    left[1] + right[1],
                    left[2] + right[2],
                )
            pending = [{**batch[0], "unscorable_reason": str(exc)[:500]}]
            return [], pending, [{"batch": batch_index, "candidate_count": 1, "error": str(exc)}]

    max_workers = min(3, max(1, config.discovery_concurrency), len(batches))
    results: list[tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]] = [([], [], []) for _ in batches]
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="radar-score") as pool:
        futures = [pool.submit(score_one, batch, index) for index, batch in enumerate(batches)]
        for index, future in enumerate(futures):
            results[index] = future.result()
    scored: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for batch_scored, batch_pending, batch_failures in results:
        scored.extend(batch_scored)
        pending.extend(batch_pending)
        failures.extend(batch_failures)
    return scored, pending, failures


def _persist_candidate_files(config: RadarConfig, candidates: list[dict[str, Any]], pending: list[dict[str, Any]]) -> None:
    def normalized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if not item.get("source"):
                item["source"] = item.get("source_id") or "unknown"
            if not item.get("fetched_at"):
                item["fetched_at"] = now_iso()
            result.append(item)
        return result

    write_jsonl(config.root / "data/candidates-latest.jsonl", normalized(candidates))
    write_jsonl(config.root / "data/candidates-unscored.jsonl", normalized(pending))


def _score_and_prepare(
    config: RadarConfig, candidates: list[dict[str, Any]], now: str, *, latest_candidates: list[dict[str, Any]] | None = None
) -> tuple[list[Event], str, str, int, list[dict[str, Any]]]:
    # Persist all rows as pending before network scoring so an interrupted run is resumable.
    _persist_candidate_files(config, latest_candidates or candidates, candidates)
    if not candidates:
        return [], "empty", "", 0, []
    scored, pending, failures = _score_candidates_in_batches(config, candidates)
    _persist_candidate_files(config, latest_candidates or candidates, pending)
    if failures and not scored:
        result = "unavailable"
        error = failures[0]["error"]
    elif failures or pending:
        result = "partial"
        error = f"{len(failures)} scoring batch(es) failed; {len(pending)} candidate(s) remain pending"
    else:
        result = "hit"
        error = ""
    events = [prepare_event(item, item.get("source", "unknown"), now, config.scoring) for item in scored]
    return events, result, error, len(pending), failures


def score_pending_candidates(config: RadarConfig) -> tuple[list[Event], dict[str, Any]]:
    pending = read_jsonl(config.root / "data/candidates-unscored.jsonl")
    latest = read_jsonl(config.root / "data/candidates-latest.jsonl")
    now = now_iso()
    if not pending:
        return [], {"scoring_result": "empty", "unscored_candidate_count": 0, "candidate_count": 0}
    from .normalization import normalize_candidate_row

    normalized = [normalize_candidate_row(row, date.today()) for row in pending]
    filtered, filter_counts = prefilter_candidates(normalized, config.scoring, today=date.today())
    scored, remaining, failures = _score_candidates_in_batches(config, filtered)
    _persist_candidate_files(config, latest or pending, remaining)
    events = [prepare_event(item, item.get("source", "unknown"), now, config.scoring) for item in scored]
    result = "unavailable" if failures and not scored else ("partial" if failures or remaining else "hit")
    return events, {
        "scoring_result": result,
        "candidate_count": len(events),
        "unscored_candidate_count": len(remaining),
        "score_failures": failures,
        "prefilter": filter_counts,
    }


def _prefilter_for_scoring(config: RadarConfig, rows: list[dict[str, Any]], anchor: date) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from .normalization import normalize_candidate_row

    normalized = [normalize_candidate_row(row, anchor) for row in rows]
    return prefilter_candidates(normalized, config.scoring, today=anchor)


def _log(config: RadarConfig, payload: dict[str, Any]) -> None:
    append_jsonl(config.logs_path, {"timestamp": now_iso(), **payload})


def _error_kind(exc: Exception) -> str:
    message = str(exc).lower()
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "authentication failed" in message or "no codex_api_key" in message:
        return "authentication"
    if "unavailable" in message or exc.__class__.__name__ == "SourceUnavailable":
        return "unavailable"
    if (
        "blocked" in message
        or "robots.txt disallows" in message
        or "http error 403" in message
        or "machportrendezvous" in message
        or "bootstrap_check_in" in message
        or exc.__class__.__name__ == "SourceBlocked"
    ):
        return "blocked"
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
        prompt = discover_prompt(source, today, config.city_scope)
        if len(prompt) > 1_200_000:
            prompt = prompt[:1_200_000]
        text, usage = client.request(
            prompt,
            retries=config.source_retries,
        )
        if source.get("id") == "llm-sweep" and usage.input_tokens > 300_000:
            raise ProviderError("llm_sweep input token cap exceeded (300000)")
        rows = parse_json_text(text)
        if not isinstance(rows, list):
            raise ProviderError(f"Source {source['id']} did not return a JSON array")
        return rows, usage, None, None
    except Exception as exc:
        return [], Usage(), _error_kind(exc), str(exc)


def discover_and_score(
    config: RadarConfig,
    *,
    fixture: Path | None = None,
    live: bool = False,
    source_ids: list[str] | None = None,
    as_of: date | None = None,
) -> tuple[list[Event], dict[str, Any]]:
    now = now_iso()
    if fixture is not None:
        fixture_data = json.loads(fixture.read_text(encoding="utf-8"))
        candidates = fixture_data.get("events", fixture_data) if isinstance(fixture_data, dict) else fixture_data
        filtered, filter_counts = _prefilter_for_scoring(config, candidates, as_of or date.today())
        _persist_candidate_files(config, candidates, [])
        events = [prepare_event(item, item.get("source", "fixture"), now, config.scoring) for item in filtered]
        hit_sources = sorted({event.source for event in events})
        _log(config, {"kind": "fixture", "source_count": 1, "candidate_count": len(events), "usage": Usage().to_dict(), "api_cost": 0})
        return events, {"source_count": 1, "source_ids": hit_sources, "candidate_count": len(events), "api_cost": 0, "source_hits": hit_sources, "source_errors": [], "prefilter": filter_counts}
    if not live:
        raise ProviderError("Live research is disabled. Pass --live or provide --fixture.")
    discovered: list[dict[str, Any]] = []
    source_count = 0
    source_hits: list[str] = []
    source_errors: list[str] = []
    source_error_details: dict[str, str] = {}
    source_error_messages: dict[str, str] = {}
    source_candidate_counts: dict[str, int] = {}
    source_empty_reasons: dict[str, str] = {}
    total_cost = 0.0
    enabled_sources = [source for source in config.sources if source.get("enabled", True) and (not source_ids or source.get("id") in source_ids)]
    source_count = len(enabled_sources)
    window_start = as_of or date.today()
    window = AdapterWindow(window_start, window_start + timedelta(days=config.window_days))
    direct_sources = [source for source in enabled_sources if source.get("adapter") in {"api", "calendar_seed", "html_list", "jsonld", "rendered", "wechat_search"}]
    llm_sources = [source for source in enabled_sources if source not in direct_sources]

    for source in direct_sources:
        try:
            adapter = get_adapter(source)
            raw_rows = adapter.fetch(source, window)
            rows = []
            for raw in raw_rows:
                from .normalization import normalize_raw_candidate

                rows.append(normalize_raw_candidate(raw, window_start))
            discovered.extend(rows)
            source_candidate_counts[source["id"]] = len(rows)
            if rows:
                source_hits.append(source["id"])
            elif source.get("empty_reason"):
                source_empty_reasons[source["id"]] = str(source["empty_reason"])
            _log(config, {"kind": "adapter_discover", "source": source["id"], "adapter": source.get("adapter"), "candidate_count": len(rows), "usage": Usage().to_dict(), "api_cost": 0})
        except Exception as exc:
            kind = _error_kind(exc)
            source_errors.append(source["id"])
            source_error_details[source["id"]] = kind
            source_error_messages[source["id"]] = str(exc)[:500]
            _log(config, {"kind": "discover_error", "source": source["id"], "adapter": source.get("adapter"), "error_kind": kind, "error": str(exc), "usage": Usage().to_dict()})

    if not llm_sources:
        filtered, filter_counts = _prefilter_for_scoring(config, discovered, window_start)
        events, scoring_result, scoring_error, pending_count, score_failures = _score_and_prepare(config, filtered, now, latest_candidates=discovered)
        _log(config, {"kind": "run_summary", "source_count": source_count, "candidate_count": len(events), "usage": Usage().to_dict(), "llm_sweep_input_token_cap": 300000, "api_cost": total_cost or None, "api_cost_status": "logged_unknown" if total_cost == 0 else "logged"})
        return events, {
            "source_count": source_count,
            "source_ids": [source["id"] for source in enabled_sources],
            "candidate_count": len(events),
            "api_cost": total_cost or None,
            "source_hits": sorted(source_hits),
            "source_errors": source_errors,
            "source_error_details": source_error_details,
            "source_error_messages": source_error_messages,
            "source_candidate_counts": source_candidate_counts,
            "source_empty_reasons": source_empty_reasons,
            "prefilter": filter_counts,
            "scoring_result": scoring_result,
            "scoring_error": scoring_error,
            "unscored_candidate_count": pending_count,
            "score_failures": score_failures,
        }

    max_workers = min(config.discovery_concurrency, max(1, source_count))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="radar-source") as pool:
        futures = {
            source["id"]: pool.submit(_discover_source, config, source, date.today())
            for source in llm_sources
        }
        for source in llm_sources:
            rows, usage, error_kind, error_message = futures[source["id"]].result()
            if error_message:
                source_errors.append(source["id"])
                health_kind = "unavailable" if error_kind == "authentication" else (error_kind or "error")
                source_error_details[source["id"]] = health_kind
                source_error_messages[source["id"]] = error_message[:500]
                _log(
                    config,
                    {
                        "kind": "discover_error",
                        "source": source["id"],
                        "error_kind": health_kind,
                        "error": error_message,
                        "usage": Usage().to_dict(),
                    },
                )
                continue
            discovered.extend(rows)
            source_candidate_counts[source["id"]] = len(rows)
            if rows:
                source_hits.append(source["id"])
            if usage.cost_usd is not None:
                total_cost += usage.cost_usd
            _log(config, {"kind": "discover", "source": source["id"], "candidate_count": len(rows), "usage": usage.to_dict(), "api_cost": usage.cost_usd})
    filtered, filter_counts = _prefilter_for_scoring(config, discovered, window_start)
    events, scoring_result, scoring_error, pending_count, score_failures = _score_and_prepare(config, filtered, now, latest_candidates=discovered)
    _log(config, {"kind": "run_summary", "source_count": source_count, "candidate_count": len(events), "usage": Usage().to_dict(), "llm_sweep_input_token_cap": 300000, "api_cost": total_cost if total_cost else None, "api_cost_status": "logged_unknown" if total_cost == 0 else "logged"})
    return events, {
        "source_count": source_count,
        "source_ids": [source["id"] for source in enabled_sources],
        "candidate_count": len(events),
        "api_cost": total_cost or None,
        "source_hits": source_hits,
        "source_errors": source_errors,
        "source_error_details": source_error_details,
        "source_error_messages": source_error_messages,
        "source_candidate_counts": source_candidate_counts,
        "source_empty_reasons": source_empty_reasons,
        "prefilter": filter_counts,
        "scoring_result": scoring_result,
        "scoring_error": scoring_error,
        "unscored_candidate_count": pending_count,
        "score_failures": score_failures,
    }
