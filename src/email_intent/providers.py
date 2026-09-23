"""Free-tier LLM providers, over the standard library only.

Supported (all have a no-cost tier):

* groq       -- console.groq.com, Llama 3.3 70B, JSON mode, very fast
* gemini     -- aistudio.google.com, Gemini 2.0 Flash, native JSON mime type
* openrouter -- openrouter.ai, any model with a ":free" suffix
* ollama     -- fully local, no account, no network egress

Providers only move bytes. Parsing, validation, normalisation and scoring all
happen in the pipeline, so adding a provider is a ~20-line change.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import PROVIDERS, Config


class LLMError(RuntimeError):
    """Any failure talking to a model: transport, auth, quota or bad payload."""


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: int


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"HTTP {error.code} from {url}: {detail}") from error
    except urllib.error.URLError as error:
        raise LLMError(f"cannot reach {url}: {error.reason}") from error
    except TimeoutError as error:
        raise LLMError(f"timed out after {timeout}s calling {url}") from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise LLMError(f"provider returned non-JSON envelope: {body[:300]}") from error


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class BaseProvider:
    name = "base"

    def __init__(self, model: str, config: Config) -> None:
        self.model = model
        self.config = config

    def api_key(self) -> str:
        env_key = PROVIDERS[self.name].env_key
        key = os.environ.get(env_key or "", "")
        if not key:
            raise LLMError(
                f"{env_key} is not set. Get a free key and put it in .env "
                f"({PROVIDERS[self.name].notes})"
            )
        return key

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


class OpenAICompatibleProvider(BaseProvider):
    """Groq and OpenRouter both speak the OpenAI chat-completions dialect."""

    endpoint = ""
    extra_headers: dict[str, str] = {}

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key()}", **self.extra_headers}
        started = time.monotonic()
        data = _post_json(self.endpoint, payload, headers, self.config.timeout_seconds)
        latency = int((time.monotonic() - started) * 1000)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError(f"unexpected response shape: {json.dumps(data)[:300]}") from error
        return LLMResponse(text or "", self.name, self.model, latency)


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    endpoint = "https://api.groq.com/openai/v1/chat/completions"


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    extra_headers = {"X-Title": "email-intent-extraction"}


class GeminiProvider(BaseProvider):
    name = "gemini"

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [{"text": message["content"]}],
            }
            for message in messages
            if message["role"] != "system"
        ]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.config.temperature,
                "responseMimeType": "application/json",
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key()}"
        )
        started = time.monotonic()
        data = _post_json(url, payload, {}, self.config.timeout_seconds)
        latency = int((time.monotonic() - started) * 1000)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError(f"unexpected response shape: {json.dumps(data)[:300]}") from error
        return LLMResponse(text, self.name, self.model, latency)


class OllamaProvider(BaseProvider):
    name = "ollama"

    def api_key(self) -> str:  # local server needs none
        return ""

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        host = os.environ.get("OLLAMA_HOST", self.config.ollama_host).rstrip("/")
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.config.temperature},
        }
        started = time.monotonic()
        data = _post_json(f"{host}/api/chat", payload, {}, self.config.timeout_seconds)
        latency = int((time.monotonic() - started) * 1000)
        text = (data.get("message") or {}).get("content")
        if text is None:
            raise LLMError(f"unexpected response shape: {json.dumps(data)[:300]}")
        return LLMResponse(text, self.name, self.model, latency)


_REGISTRY: dict[str, type[BaseProvider]] = {
    "groq": GroqProvider,
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
}


def get_provider(name: str, model: str, config: Config) -> BaseProvider | None:
    """Instantiate a provider. Returns None for the offline rule engine."""
    if name == "heuristic":
        return None
    provider_class = _REGISTRY.get(name)
    if provider_class is None:
        raise LLMError(f"no client implemented for provider {name!r}")
    return provider_class(model, config)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply, tolerating fences and preamble.

    Small models sometimes wrap JSON in prose or a code fence even when asked
    not to. Rather than failing the whole extraction, recover the object.
    """
    if not text or not text.strip():
        raise LLMError("model returned an empty response")

    candidate = text.strip()
    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = _first_balanced_object(candidate)

    if not isinstance(parsed, dict):
        raise LLMError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _first_balanced_object(text: str) -> Any:
    start = text.find("{")
    if start == -1:
        raise LLMError(f"no JSON object found in model reply: {text[:200]}")
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                chunk = text[start:index + 1]
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError as error:
                    raise LLMError(f"malformed JSON in model reply: {error}") from error
    raise LLMError("model reply contains an unterminated JSON object")
