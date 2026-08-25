"""Adapter cap-contract tests (M3 #1524, GATE-2): the per-call ``max_tokens``
kwarg reaches the request body (race-free — no shared-mutable fallback), and
``last_finish_reason`` is surfaced from the provider response so the
extractor's truncation detection works end-to-end.

Covers the production adapters (OpenRouterModel / DeepSeekDirectModel /
RoutingModel via build_extractor_model) + the sdk offline seam.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.model_adapters import (  # noqa: E402, I001, RUF100
    DeepSeekDirectModel,
    OpenRouterModel,
    build_extractor_model,
)


class _FakeResponse:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _chat_data(content: str = "hello", finish_reason: str = "stop") -> dict:
    return {
        "choices": [{"message": {"content": content},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_openrouter_max_tokens_kwarg_reaches_body(monkeypatch):
    captured: dict = {}

    def _fake_post(self_or_url, url=None, headers=None, json=None, timeout=None, **kwargs):
        captured["body"] = json
        return _FakeResponse(_chat_data())

    monkeypatch.setattr("tortoise.model_adapters.requests.Session.post", _fake_post)
    model = OpenRouterModel("deepseek/deepseek-v4-flash", max_tokens=8000)
    model.complete(system="s", user="u", max_tokens=1500)
    assert captured["body"]["max_tokens"] == 1500
    # per-call override wins; None → the constructor default applies
    model.complete(system="s", user="u")
    assert captured["body"]["max_tokens"] == 8000


def test_deepseek_direct_max_tokens_kwarg_reaches_body(monkeypatch):
    captured: dict = {}

    def _fake_post(self_or_url, url=None, headers=None, json=None, timeout=None, **kwargs):
        captured["body"] = json
        return _FakeResponse(_chat_data())

    monkeypatch.setattr("tortoise.model_adapters.requests.Session.post", _fake_post)
    model = DeepSeekDirectModel("deepseek-v4-flash", max_tokens=4000)
    model.complete(system="s", user="u", max_tokens=1500)
    assert captured["body"]["max_tokens"] == 1500


@pytest.mark.parametrize("finish_reason,expected", [
    ("length", "length"), ("stop", "stop"), (None, None),
])
def test_openrouter_last_finish_reason(monkeypatch, finish_reason, expected):
    monkeypatch.setattr(
        "tortoise.model_adapters.requests.Session.post",
        lambda *a, **k: _FakeResponse(_chat_data(finish_reason=finish_reason)))
    model = OpenRouterModel("m")
    model.complete(system="s", user="u")
    assert model.last_finish_reason == expected


def test_routing_model_forwards_cap_and_finish_reason(monkeypatch):
    """The production routing adapter (build_extractor_model — the sdk
    capture path and the v2-ingest runner both use it) forwards the per-call
    cap to the inner adapter and surfaces last_finish_reason."""
    captured: dict = {}

    def _fake_post(self_or_url, url=None, headers=None, json=None, timeout=None, **kwargs):
        captured["body"] = json
        return _FakeResponse(_chat_data(finish_reason="length"))

    monkeypatch.setattr("tortoise.model_adapters.requests.Session.post", _fake_post)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    routing = build_extractor_model("deepseek/deepseek-v4-flash",
                                    max_tokens=8000)
    routing.complete(system="s", user="u", max_tokens=1500)
    assert captured["body"]["max_tokens"] == 1500
    assert routing.last_finish_reason == "length"


def test_sdk_v2_mock_accepts_max_tokens_kwarg():
    """The sdk offline seam (TORTOISE_SESSION_LLM_MOCK=1) accepts the kwarg
    (deterministic — the cap is ignored) so _complete's kwarg path never
    falls to the unbounded warning on the mock path."""
    from tortoise.sdk import _V2SessionMock

    m = _V2SessionMock()
    out = m.complete(system="STORY SUMMARIZER v1", user="u", max_tokens=1500)
    assert "strategy" in out
