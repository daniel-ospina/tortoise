"""Tests for #278 — Ollama local mode for PII-sensitive metadata extraction.

Covers the `ollama:MODEL` provider-spec routing in session_indexer
(extract_metadata_with_llm / extract_metadata): local endpoint routing, the
whitelist exemption, no-API-key behavior, failure fallback, and OpenAI-path
regression. HTTP is mocked at urllib level (same pattern as
tests/test_supplementary.py test_ollama_complete_mocked) so no Ollama server
or network is required.

Runnable with: .venv/bin/python -m pytest tests/test_session_indexer_ollama.py -v
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.session_indexer import (
    _ALLOWED_LLM_MODELS,
    _parse_llm_json,
    extract_metadata,
    extract_metadata_with_llm,
)

SESSION_CONTENT = """---
title: Test session
---
## User
What is 2+2?
## Assistant
Four.
"""


class FakeOllamaResponse:
    """Ollama /api/chat-shaped response (message.content)."""

    def __init__(self, content: str):
        self._content = content
        self.url = None
        self.body = None

    def read(self):
        return json.dumps({"message": {"content": self._content}}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture
def capture_ollama_request(monkeypatch):
    """Patch urllib to capture the request and return a canned Ollama response."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        payload = {
            "narrative_arc": [
                {"phase": "Act 1 — Question", "topic": "Arithmetic",
                 "decisions": [], "message_range": [1, 2]}
            ],
            "summary": "A session about basic arithmetic.",
            "keywords": ["arithmetic", "question"],
            "topics": ["math"],
            "issues": [],
            "prs": [],
            "critical_decisions": [],
        }
        return FakeOllamaResponse(json.dumps(payload))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


# ── ollama: spec routing ─────────────────────────────────────────


def test_ollama_spec_routes_to_local_endpoint(capture_ollama_request, monkeypatch):
    """`ollama:MODEL` hits the native local /api/chat endpoint with format json."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = extract_metadata_with_llm(SESSION_CONTENT, "ollama:llama3.2:3b")

    assert capture_ollama_request["url"] == "http://localhost:11434/api/chat"
    body = capture_ollama_request["body"]
    assert body["model"] == "llama3.2:3b"
    assert body["format"] == "json"
    assert body["stream"] is False
    assert result is not None
    assert result["summary"] == "A session about basic arithmetic."
    assert result["keywords"] == ["arithmetic", "question"]


def test_ollama_requires_no_api_key(capture_ollama_request, monkeypatch):
    """Local mode works with NO OPENAI_API_KEY set — PII stays on the machine."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")  # empty key must not matter either

    result = extract_metadata_with_llm(SESSION_CONTENT, "ollama:qwen3:0.6b")

    assert result is not None
    assert capture_ollama_request["body"]["model"] == "qwen3:0.6b"


def test_ollama_tag_with_inner_colon(capture_ollama_request):
    """Model part may contain ':' (ollama tags) — full tag survives the split."""
    result = extract_metadata_with_llm(SESSION_CONTENT, "ollama:llama3.2:3b")
    assert result is not None
    assert capture_ollama_request["body"]["model"] == "llama3.2:3b"


def test_ollama_malformed_json_returns_none(monkeypatch):
    """Malformed LLM output → None (caller falls back), never a crash."""

    def fake_urlopen(req, timeout=None):
        return FakeOllamaResponse("not json at all")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert extract_metadata_with_llm(SESSION_CONTENT, "ollama:test") is None


def test_ollama_request_failure_returns_none(monkeypatch):
    """Connection failure → None (caller falls back)."""

    def fake_urlopen(req, timeout=None):
        raise ConnectionRefusedError("no local ollama")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert extract_metadata_with_llm(SESSION_CONTENT, "ollama:test") is None


def test_ollama_empty_model_part_rejected():
    """`ollama:` with no model part → error dict (no empty-id request sent)."""
    result = extract_metadata_with_llm(SESSION_CONTENT, "ollama:")
    assert isinstance(result, dict)
    assert "error" in result
    assert "missing model part" in result["error"]


# ── tiered extract_metadata integration ──────────────────────────


def test_extract_metadata_ollama_tier2_success(capture_ollama_request):
    """extract_metadata(..., "ollama:...") returns the LLM result (Tier 2)."""
    result = extract_metadata(SESSION_CONTENT, "ollama:llama3.2:3b")
    assert result["summary"] == "A session about basic arithmetic."
    assert "arithmetic" in result["keywords"]
    assert len(result["narrative_arc"]) == 1


def test_extract_metadata_ollama_failure_falls_back(monkeypatch):
    """Ollama unavailable → Tier 3 keyword fallback (existing behavior)."""

    def fake_urlopen(req, timeout=None):
        raise ConnectionRefusedError("no local ollama")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = extract_metadata(SESSION_CONTENT, "ollama:test")
    assert "summary" in result
    assert "keywords" in result
    assert isinstance(result["narrative_arc"], list)


# ── whitelist + OpenAI regression ────────────────────────────────


def test_non_whitelisted_openai_model_still_rejected():
    """Non-whitelisted OpenAI model → error dict (unchanged behavior)."""
    result = extract_metadata_with_llm(SESSION_CONTENT, "gpt-4-turbo")
    assert isinstance(result, dict)
    assert "error" in result


def test_whitelisted_model_without_key_returns_none(monkeypatch):
    """Whitelisted OpenAI model + missing key → None (fallback path unchanged)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert extract_metadata_with_llm(SESSION_CONTENT, "gpt-5-mini") is None


def test_whitelist_still_contains_default_models():
    """The OpenAI whitelist is intact — no models silently removed."""
    assert "gpt-5-mini" in _ALLOWED_LLM_MODELS
    assert "gpt-4o-mini" in _ALLOWED_LLM_MODELS
    assert "gpt-4o" in _ALLOWED_LLM_MODELS


# ── parse helper ─────────────────────────────────────────────────


def test_parse_llm_json_strips_fences_and_validates():
    result = _parse_llm_json('```json\n{"keywords": ["a"], "issues": "not-a-list"}\n```')
    assert result["keywords"] == ["a"]
    # non-list coerced to list
    assert result["issues"] == []
    assert _parse_llm_json("garbage") is None
    assert _parse_llm_json("[1,2,3]") is None  # not a dict
