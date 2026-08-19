from __future__ import annotations

from typing import Any

from .base import SourceAdapter
from .calendar_seed import CalendarSeedAdapter
from .generic import HtmlListAdapter, JsonLdAdapter, PublicJsonAdapter, RenderedAdapter, WechatSearchAdapter
from .onepilot import OnePilotAdapter


def get_adapter(source: dict[str, Any]) -> SourceAdapter:
    adapter = str(source.get("adapter") or "").strip()
    if adapter == "api" and source.get("id") == "onepilot":
        return OnePilotAdapter()
    if adapter == "calendar_seed":
        return CalendarSeedAdapter()
    if adapter == "html_list":
        return HtmlListAdapter()
    if adapter == "jsonld":
        return JsonLdAdapter()
    if adapter == "api":
        return PublicJsonAdapter()
    if adapter == "rendered":
        return RenderedAdapter()
    if adapter == "wechat_search":
        return WechatSearchAdapter()
    raise RuntimeError(f"Adapter {adapter or '<missing>'} is not implemented for {source.get('id', '<unknown>')}")
