from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def load_local_env(root: Path) -> None:
    paths = [root / ".env", Path.home() / ".hermes/.env"]
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in {"CODEX_API_KEY", "OPENAI_API_KEY", "LM_API_KEY", "CODEX_BASE_URL", "OPENAI_BASE_URL", "RADAR_MODEL", "RADAR_INPUT_PRICE_PER_MILLION", "RADAR_OUTPUT_PRICE_PER_MILLION"} and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
        }


class ProviderError(RuntimeError):
    pass


def load_codex_auth_key() -> str | None:
    """Read the local Codex auth file without logging or exporting its secret."""
    path = Path.home() / ".codex/auth.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("OPENAI_API_KEY") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value.strip() else None


class OpenAIResponsesClient:
    def __init__(self, base_url: str, model: str, root: Path, timeout: int = 180) -> None:
        load_local_env(root)
        self.api_key = os.getenv("CODEX_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("LM_API_KEY") or load_codex_auth_key()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.last_usage = Usage()
    def _cost(self, usage: dict[str, Any]) -> float | None:
        input_price = os.getenv("RADAR_INPUT_PRICE_PER_MILLION")
        output_price = os.getenv("RADAR_OUTPUT_PRICE_PER_MILLION")
        if not input_price or not output_price:
            return None
        try:
            return (int(usage.get("input_tokens", 0)) * float(input_price) + int(usage.get("output_tokens", 0)) * float(output_price)) / 1_000_000
        except ValueError:
            return None

    def request(self, prompt: str, *, web_search: bool = True, retries: int = 3) -> tuple[str, Usage]:
        if not self.api_key:
            raise ProviderError("No CODEX_API_KEY, OPENAI_API_KEY, or LM_API_KEY was found")
        payload: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
        }
        if web_search:
            payload["tools"] = [{"type": "web_search"}]
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                usage_data = data.get("usage", {}) or {}
                usage = Usage(
                    input_tokens=int(usage_data.get("input_tokens", 0)),
                    output_tokens=int(usage_data.get("output_tokens", 0)),
                    cost_usd=self._cost(usage_data),
                )
                self.last_usage = usage
                text = data.get("output_text") or extract_output_text(data)
                if not text:
                    raise ProviderError("Responses API returned no output text")
                return text, usage
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    raise ProviderError(f"Responses API authentication failed with HTTP {exc.code}; check the configured Codex/OpenAI key and base URL") from exc
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(2**attempt)
            except (urllib.error.URLError, TimeoutError, ProviderError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(2**attempt)
        raise ProviderError(f"Responses API failed after {retries} attempts: {last_error}") from last_error


def extract_output_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(str(content["text"]))
    return "\n".join(chunks).strip()


def parse_json_text(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min([index for index in (cleaned.find("["), cleaned.find("{")) if index >= 0], default=-1)
        end = max(cleaned.rfind("]"), cleaned.rfind("}"))
        if start < 0 or end <= start:
            raise
        return json.loads(cleaned[start:end + 1])
