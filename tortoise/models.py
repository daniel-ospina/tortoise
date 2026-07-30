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

import json
import os
import urllib.request
from typing import Protocol, runtime_checkable


@runtime_checkable
class Model(Protocol):
    id: str
    def complete(self, *, system: str, user: str) -> str: ...


class OpenAICompatModel:
    def __init__(self, *, id: str, base_url: str, api_key_env: str | None = "OPENAI_API_KEY",
                 temperature: float = 0.0, timeout: int = 60):
        # api_key_env=None → no Authorization header.
        self.id = id
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.timeout = timeout

    def build_request(self, system: str, user: str) -> dict:
        return {
            "model": self.id,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

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
            return self.parse_response(json.loads(r.read()))


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
