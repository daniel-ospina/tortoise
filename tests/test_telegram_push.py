"""Tests for tortoise/telegram_push.py — standalone Telegram push module."""

from __future__ import annotations

import httpx
import pytest

from tortoise.telegram_push import (
    TelegramPushError,
    send_message,
)


# ── Unit tests ───────────────────────────────────────────────────────────────

def test_send_message_happy_path(monkeypatch):
    """send_message returns the Telegram response dict on success."""
    calls = []

    def mock_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        # Return a fake response-like object
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"ok": True, "result": {"message_id": 42}}
        return FakeResponse()

    monkeypatch.setattr("tortoise.telegram_push.httpx.post", mock_post)
    result = send_message("tok123", "chat456", "hello")
    assert result == {"ok": True, "result": {"message_id": 42}}
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/bottok123/sendMessage"
    assert calls[0]["json"] == {"chat_id": "chat456", "text": "hello"}


def test_send_message_api_error(monkeypatch):
    """send_message raises TelegramPushError when Telegram returns ok=false."""

    def mock_post(url, json=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"ok": False, "description": "chat not found"}
        return FakeResponse()

    monkeypatch.setattr("tortoise.telegram_push.httpx.post", mock_post)
    with pytest.raises(TelegramPushError, match="ok=false.*chat not found"):
        send_message("tok", "bad", "hi")


def test_send_message_http_error(monkeypatch):
    """send_message raises TelegramPushError on HTTP 5xx."""

    def mock_post(url, json=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "server error",
                    request=httpx.Request("POST", url),
                    response=httpx.Response(503, text="Service Unavailable"),
                )
        return FakeResponse()

    monkeypatch.setattr("tortoise.telegram_push.httpx.post", mock_post)
    with pytest.raises(TelegramPushError, match="telegram api 503"):
        send_message("tok", "chat", "hi")


def test_send_message_network_error(monkeypatch):
    """send_message raises TelegramPushError on connection failure."""

    def mock_post(url, json=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("tortoise.telegram_push.httpx.post", mock_post)
    with pytest.raises(TelegramPushError, match="connection refused"):
        send_message("tok", "chat", "hi")


def test_send_message_timeout(monkeypatch):
    """send_message raises TelegramPushError on request timeout."""

    def mock_post(url, json=None, timeout=None):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("tortoise.telegram_push.httpx.post", mock_post)
    with pytest.raises(TelegramPushError, match="timed out"):
        send_message("tok", "chat", "hi")


def test_chat_id_coerced_to_string(monkeypatch):
    """Integer chat_ids are converted to string for the JSON payload."""
    calls = []

    def mock_post(url, json=None, timeout=None):
        calls.append(json)
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"ok": True, "result": {}}
        return FakeResponse()

    monkeypatch.setattr("tortoise.telegram_push.httpx.post", mock_post)
    send_message("tok", 123456, "hi")  # int chat_id
    assert calls[0]["chat_id"] == "123456"
