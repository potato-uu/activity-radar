from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.robotparser
from datetime import date
from typing import Any, Iterable

from bs4 import BeautifulSoup

from .base import AdapterWindow, RawCandidate
from .http import RateLimitedHttpClient


class SourceUnavailable(RuntimeError):
    pass


class SourceBlocked(RuntimeError):
    pass


def _urls(source: dict[str, Any]) -> list[str]:
    values = source.get("urls") or source.get("url") or []
    return [str(value) for value in (values if isinstance(values, list) else [values]) if value]


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("text") or "").strip()
    if isinstance(value, list):
        return " ".join(_text(item) for item in value if _text(item)).strip()
    return str(value or "").strip()


def _iso_day(value: Any) -> str:
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", _text(value))
    if not match:
        return ""
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return ""


def _city(text: str, fallback: str = "") -> str:
    aliases = {"Shanghai": "上海", "Hangzhou": "杭州", "Suzhou": "苏州", "Nanjing": "南京", "Ningbo": "宁波", "Wuxi": "无锡", "Hefei": "合肥", "Jiaxing": "嘉兴", "Nantong": "南通"}
    for city in ("上海", "杭州", "苏州", "南京", "宁波", "无锡", "合肥", "嘉兴", "南通"):
        if city in text:
            return city
    for english, chinese in aliases.items():
        if english.lower() in text.lower():
            return chinese
    return fallback


def _event_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _event_nodes(item)
    elif isinstance(value, dict):
        types = value.get("@type") or []
        type_values = types if isinstance(types, list) else [types]
        if any(str(item).lower() == "event" for item in type_values):
            yield value
        for key in ("@graph", "itemListElement"):
            if key in value:
                yield from _event_nodes(value[key])
        if isinstance(value.get("item"), dict):
            yield from _event_nodes(value["item"])


def extract_jsonld_candidates(html: str, source: dict[str, Any], fetched_at: str) -> list[RawCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    base_url = _urls(source)[0] if _urls(source) else ""
    rows: list[RawCandidate] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text() or "")
        except json.JSONDecodeError:
            continue
        for node in _event_nodes(value):
            location = node.get("location") or {}
            address = location.get("address") if isinstance(location, dict) else {}
            locality = _text(address.get("addressLocality")) if isinstance(address, dict) else ""
            venue = " ".join(part for part in (_text(location), _text(address)) if part).strip()
            url = urllib.parse.urljoin(base_url, _text(node.get("url")))
            title = _text(node.get("name"))
            if not title or not url:
                continue
            rows.append(
                RawCandidate(
                    source_id=str(source.get("id") or "jsonld"),
                    raw_title=title,
                    raw_date_text=_text(node.get("startDate")),
                    date_start=_iso_day(node.get("startDate")),
                    date_end=_iso_day(node.get("endDate")) or _iso_day(node.get("startDate")),
                    city=_city(f"{locality} {venue}", locality),
                    venue=venue,
                    organizer=_text(node.get("organizer")),
                    url=url,
                    raw_excerpt=_text(node.get("description"))[:600],
                    event_type="",
                    fetched_at=fetched_at,
                    date_precision="day" if _iso_day(node.get("startDate")) else "unknown",
                )
            )
    return rows


def _select_text(node: Any, selector: str | None) -> str:
    if not selector:
        return ""
    selected = node.select_one(selector)
    return selected.get_text(" ", strip=True) if selected else ""


def extract_html_candidates(html: str, source: dict[str, Any], fetched_at: str) -> list[RawCandidate]:
    if not (source.get("params") or {}).get("prefer_selectors"):
        jsonld = extract_jsonld_candidates(html, source, fetched_at)
        if jsonld:
            return jsonld
    soup = BeautifulSoup(html, "html.parser")
    selectors = source.get("selectors") or {}
    base_url = _urls(source)[0] if _urls(source) else ""
    rows: list[RawCandidate] = []
    if selectors.get("item"):
        nodes = soup.select(str(selectors["item"]))
    else:
        nodes = [anchor.parent for anchor in soup.select("a[href]") if anchor.parent]
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        title_node = node.select_one(str(selectors.get("title"))) if selectors.get("title") else node.select_one("a[href]")
        title = title_node.get_text(" ", strip=True) if title_node else ""
        date_text = _select_text(node, selectors.get("date")) or node.get_text(" ", strip=True)
        if not title or not re.search(r"20\d{2}[-./年]\d{1,2}|\d{1,2}月\d{1,2}日", date_text):
            continue
        url_node = node.select_one(str(selectors.get("url"))) if selectors.get("url") else title_node
        href = url_node.get("href") if url_node else ""
        url = urllib.parse.urljoin(base_url, str(href or ""))
        key = (title, url)
        if not url or key in seen:
            continue
        seen.add(key)
        venue = _select_text(node, selectors.get("venue"))
        excerpt = _select_text(node, selectors.get("excerpt")) or node.get_text(" ", strip=True)
        region_hint = source.get("region_hint") or []
        fallback = str(region_hint[0]) if isinstance(region_hint, list) and len(region_hint) == 1 else ""
        rows.append(
            RawCandidate(
                source_id=str(source.get("id") or "html"),
                raw_title=title,
                raw_date_text=date_text[:300],
                city=_city(f"{venue} {excerpt}", fallback),
                venue=venue,
                organizer=_select_text(node, selectors.get("organizer")),
                url=url,
                raw_excerpt=excerpt[:600],
                event_type="",
                fetched_at=fetched_at,
            )
        )
        if len(rows) >= int((source.get("params") or {}).get("max_candidates", 100)):
            break
    return rows


def _path(value: Any, path: str) -> Any:
    current = value
    for part in [item for item in path.split(".") if item]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


def extract_json_rows(payload: Any, source: dict[str, Any], fetched_at: str) -> list[RawCandidate]:
    params = source.get("params") or {}
    rows = _path(payload, str(params.get("items_path") or "")) if params.get("items_path") else payload
    if not isinstance(rows, list):
        return []
    field_map = params.get("field_map") or {}
    base_url = _urls(source)[0] if _urls(source) else ""
    result: list[RawCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        get = lambda field: _path(row, str(field_map.get(field) or field))
        title = _text(get("title"))
        url = urllib.parse.urljoin(base_url, _text(get("url")))
        if not title or not url:
            continue
        start = _iso_day(get("date_start"))
        result.append(
            RawCandidate(
                source_id=str(source.get("id") or "api"),
                raw_title=title,
                raw_date_text=_text(get("raw_date_text") or get("date_start")),
                date_start=start,
                date_end=_iso_day(get("date_end")) or start,
                city=_text(get("city")),
                venue=_text(get("venue")),
                organizer=_text(get("organizer")),
                url=url,
                raw_excerpt=_text(get("excerpt"))[:600],
                event_type=_text(get("event_type")),
                fetched_at=fetched_at,
                date_precision="day" if start else "unknown",
            )
        )
    return result


class _GenericBase:
    _robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _client(self, source: dict[str, Any]) -> RateLimitedHttpClient:
        params = source.get("params") or {}
        return RateLimitedHttpClient(interval_seconds=float(params.get("request_interval_seconds", 2)), timeout_seconds=int(params.get("timeout_seconds", 45)))

    def _check_robots(self, client: RateLimitedHttpClient, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            robots_url = f"{root}/robots.txt"
            try:
                text = client.get_text(robots_url)
                parser = urllib.robotparser.RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(text.splitlines())
                self._robots[root] = parser
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                self._robots[root] = None
        parser = self._robots[root]
        if parser is not None and not parser.can_fetch("activity-radar/0.2", url):
            raise SourceBlocked(f"robots.txt disallows {parsed.path or '/'}")

    def _within(self, rows: list[RawCandidate], window: AdapterWindow) -> list[RawCandidate]:
        result: list[RawCandidate] = []
        for row in rows:
            if not row.date_start:
                result.append(row)
                continue
            try:
                start = date.fromisoformat(row.date_start[:10])
            except ValueError:
                result.append(row)
                continue
            if window.start <= start <= window.end:
                result.append(row)
        return result


class HtmlListAdapter(_GenericBase):
    def fetch(self, config: dict[str, Any], window: AdapterWindow) -> list[RawCandidate]:
        from ..research import now_iso

        client = self._client(config)
        rows: list[RawCandidate] = []
        errors: list[Exception] = []
        for url in _urls(config):
            try:
                self._check_robots(client, url)
                rows.extend(extract_html_candidates(client.get_text(url), {**config, "url": url}, now_iso()))
            except Exception as exc:
                errors.append(exc)
        if errors and len(errors) == len(_urls(config)):
            raise errors[-1]
        return self._within(rows, window)


class JsonLdAdapter(HtmlListAdapter):
    pass


class PublicJsonAdapter(_GenericBase):
    def fetch(self, config: dict[str, Any], window: AdapterWindow) -> list[RawCandidate]:
        from ..research import now_iso

        client = self._client(config)
        rows: list[RawCandidate] = []
        errors: list[Exception] = []
        for url in _urls(config):
            try:
                self._check_robots(client, url)
                rows.extend(extract_json_rows(client.get_json(url), {**config, "url": url}, now_iso()))
            except Exception as exc:
                errors.append(exc)
        if errors and len(errors) == len(_urls(config)):
            raise errors[-1]
        return self._within(rows, window)


class RenderedAdapter(_GenericBase):
    def fetch(self, config: dict[str, Any], window: AdapterWindow) -> list[RawCandidate]:
        if not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
            raise SourceBlocked("PLAYWRIGHT_BROWSERS_PATH is not set")
        from playwright.sync_api import sync_playwright
        from ..research import now_iso

        client = self._client(config)
        rows: list[RawCandidate] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                for url in _urls(config):
                    self._check_robots(client, url)
                    time.sleep(2)
                    page.goto(url, wait_until="domcontentloaded", timeout=int((config.get("params") or {}).get("timeout_seconds", 45)) * 1000)
                    page.wait_for_timeout(int((config.get("params") or {}).get("render_wait_ms", 2500)))
                    rows.extend(extract_html_candidates(page.content(), {**config, "url": page.url}, now_iso()))
            finally:
                browser.close()
        return self._within(rows, window)


class WechatSearchAdapter:
    def fetch(self, config: dict[str, Any], window: AdapterWindow) -> list[RawCandidate]:
        raise SourceUnavailable("agent-reach is installed but miku_ai is unavailable")
