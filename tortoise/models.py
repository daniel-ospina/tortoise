"""Model backends for the extractor stages.

A Model is a text-in / text-out completer. Stages own the prompts and parsing, so
swapping a model is a config change, not a code change — which is what lets us
benchmark "what we can get away with" per stage (cheap model for point extraction,
large reasoning model for relations).

`OpenAICompatModel` covers DeepSeek, Gemini (OpenAI-compat endpoint), and local
Ollama with one adapter. Its transport (`complete`) is thin and untested without a
network/key; the tested surface is `build_request` / `parse_response`.
"""
from __future__ import annotations

import contextlib
import json
import os
import urllib.request
from typing import Protocol, runtime_checkable


def _emit_usage_sink(model, usage) -> None:
    """#2185 seam: optional usage-capture sink fire — strict NO-OP when unset.

    Called at the response-parse site of every real chat transport with the
    RESPONSE-LOCAL usage block (the per-attempt billed totals, incl.
    cache-detail fields when the provider sends them). Payload is keyword:
    ``(provider, model_id, usage, usage_present)`` — the SAME contract on all
    four seam sites (#2185 A1):

    - ``provider`` — the adapter's own class attribute where it exists
      (openrouter/venice/deepseek-direct); ``None`` on provider-less classes
      (OpenAICompatModel, OfficialJudgeModel) — the harness binds provider at
      registration time, never on the shared product class (Am 9).
    - ``usage`` — the response usage dict (may be None / {} when the provider
      sent none).
    - ``usage_present`` — ``bool(usage)`` (False = non-billing lane / no usage
      block; the call still counts).
    """
    sink = getattr(model, "usage_sink", None)
    if sink is None:
        return
    with contextlib.suppress(Exception):
        # round-2 code-review P2: a metering observer must NEVER flip a call
        # outcome — a raising/poisoned sink degrades to a silent no-op at
        # the fire site (the provider response was already parsed).
        sink(provider=getattr(model, "provider", None),
             model_id=model.id, usage=usage, usage_present=bool(usage))


@runtime_checkable
class Model(Protocol):
    id: str
    def complete(self, *, system: str, user: str) -> str: ...


_UNSET = object()


class OpenAICompatModel:
    def __init__(self, *, id: str, base_url: str, api_key_env: str | None = "OPENAI_API_KEY",
                 temperature: float = 0.0, timeout: int = 60,
                 response_format: dict | None | object = _UNSET,  # noqa: RUF036
                 max_tokens: int | None = None):
        # api_key_env=None → no Authorization header.
        self.id = id
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.timeout = timeout
        # response_format defaults to the legacy JSON-object mode so existing
        # extraction callers are unaffected; pass response_format=None to opt
        # out (the LongMemEval reader must NOT force JSON — the official
        # benchmark call shape has no response_format). max_tokens=None → the
        # key is omitted from the request body.
        self.response_format = (
            {"type": "json_object"} if response_format is _UNSET else response_format)
        self.max_tokens = max_tokens
        # #2185: additive usage-capture seam (no-op unless the harness sets
        # it). NO last_* mirrors on this class — it is shared with the product
        # paths (sdk.py/ingest.py/mining.py) and the eval owns its metering.
        self.usage_sink = None

    def build_request(self, system: str, user: str) -> dict:
        req = {
            "model": self.id,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.response_format is not None:
            req["response_format"] = self.response_format
        if self.max_tokens is not None:
            req["max_tokens"] = self.max_tokens
        return req

    @staticmethod
    def parse_response(data: dict) -> str:
        return data["choices"][0]["message"]["content"]

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise RuntimeError(
                    f"{self.id}: env var {self.api_key_env} is not set")
            h["Authorization"] = f"Bearer {key}"
        return h

    def complete(self, *, system: str, user: str) -> str:
        body = json.dumps(self.build_request(system, user)).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        # #2185 seam: fire with the response-local usage (provider None here —
        # bound at registration by the harness; no mirrors on this class).
        _emit_usage_sink(self, data.get("usage"))
        return self.parse_response(data)


class OllamaModel:
    """Native Ollama /api/chat with a `think` toggle. The OpenAI-compat endpoint
    can't control qwen3's thinking, so we hit the native endpoint directly.
    think=False for the mechanical point tier (thinking is wasted and ~50x slower);
    think=True for the relation tier, which needs reasoning to find operators."""

    def __init__(self, *, id: str, base_url: str = "http://localhost:11434",
                 timeout: int = 300, think: bool = False, temperature: float = 0.0):
        self.id = id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.think = think
        self.temperature = temperature

    def build_request(self, system: str, user: str) -> dict:
        req = {
            "model": self.id,
            "think": self.think,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if "options" not in req:
            req["options"] = {}
        req["options"]["temperature"] = self.temperature
        return req

    @staticmethod
    def parse_response(data: dict) -> str:
        return data["message"]["content"]

    def complete(self, *, system: str, user: str) -> str:
        body = json.dumps(self.build_request(system, user)).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return self.parse_response(json.loads(r.read()))
