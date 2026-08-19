from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any


class RateLimitedHttpClient:
    _locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
    _last_request: dict[str, float] = {}

    def __init__(self, *, interval_seconds: float = 2.0, timeout_seconds: int = 45) -> None:
        self.interval_seconds = max(2.0, interval_seconds)
        self.timeout_seconds = timeout_seconds

    def get_text(self, url: str, *, headers: dict[str, str] | None = None) -> str:
        host = urllib.parse.urlparse(url).netloc.lower()
        lock = self._locks[host]
        with lock:
            elapsed = time.monotonic() - self._last_request.get(host, 0.0)
            if elapsed < self.interval_seconds:
                time.sleep(self.interval_seconds - elapsed)
            request_headers = {
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "User-Agent": "activity-radar/0.2 (+public event discovery; no login)",
                **(headers or {}),
            }
            request = urllib.request.Request(url, headers=request_headers, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read().decode("utf-8", errors="replace")
            finally:
                self._last_request[host] = time.monotonic()

    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        return json.loads(self.get_text(url, headers=headers))
